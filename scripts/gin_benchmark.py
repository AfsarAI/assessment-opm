#!/usr/bin/env python3
"""
BENCHMARK: GIN Index Drop+Rebuild vs. Inline GIN maintenance.
Tests the core optimization for the 500K import bottleneck.
"""

import io
import csv
import time
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://opm_user:opm_password@db:5432/opm_db"
engine = create_engine(DB, echo=False)
CSV_FILE = "/app/products.csv"
STAGING = "gin_bench_staging"

def load_staging():
    """Load CSV into staging table."""
    # Drop + create
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(f"DROP TABLE IF EXISTS {STAGING};"))
            conn.execute(text(f"""
                CREATE UNLOGGED TABLE {STAGING} (
                    row_num INT, name VARCHAR(255), sku VARCHAR(100), description TEXT
                );
            """))

    # Copy data
    batch = []
    raw = engine.raw_connection()
    cur = raw.cursor()
    with open(CSV_FILE, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader)
        for i, row in enumerate(reader, 1):
            if len(row) < 3 or not row[0].strip() or not row[1].strip():
                continue
            batch.append((i, row[0].strip(), row[1].strip(), (row[2].strip() if len(row) > 2 else "")))
            if len(batch) >= 25000:
                buf = io.StringIO()
                for r in batch:
                    buf.write(f"{r[0]}\t{r[1].replace(chr(9),' ')}\t{r[2].replace(chr(9),' ')}\t{r[3].replace(chr(9),' ')}\n")
                buf.seek(0)
                with cur.copy(f"COPY {STAGING} (row_num, name, sku, description) FROM STDIN") as copy:
                    copy.write(buf.getvalue())
                raw.commit()
                batch.clear()
    if batch:
        buf = io.StringIO()
        for r in batch:
            buf.write(f"{r[0]}\t{r[1].replace(chr(9),' ')}\t{r[2].replace(chr(9),' ')}\t{r[3].replace(chr(9),' ')}\n")
        buf.seek(0)
        with cur.copy(f"COPY {STAGING} (row_num, name, sku, description) FROM STDIN") as copy:
            copy.write(buf.getvalue())
        raw.commit()
    cur.close()
    raw.close()
    print(f"  Staging loaded: {STAGING}")

def reset_products():
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("TRUNCATE products;"))
            # Ensure GIN indexes exist
            conn.execute(text("DROP INDEX IF EXISTS ix_products_name_trgm;"))
            conn.execute(text("DROP INDEX IF EXISTS ix_products_sku_trgm;"))
            conn.execute(text("CREATE INDEX ix_products_name_trgm ON products USING gin (name gin_trgm_ops);"))
            conn.execute(text("CREATE INDEX ix_products_sku_trgm ON products USING gin (sku gin_trgm_ops);"))

def check_indexes():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename='products' ORDER BY indexname;"))
        return [row[0] for row in result.fetchall()]

# ─── BENCHMARK A: Current approach (GIN inline) ─────────────────────────────
print("\n=== BENCHMARK A: CURRENT — UPSERT with GIN Indexes Active ===")
load_staging()
reset_products()
print(f"  Indexes: {check_indexes()}")

t0 = time.perf_counter()
with engine.connect() as conn:
    conn.execute(text("SET work_mem = '128MB';"))
    conn.execute(text(f"""
        INSERT INTO products (sku, name, description, active, created_at, updated_at)
        SELECT sku, name, description, TRUE, NOW(), NOW()
        FROM (
            SELECT DISTINCT ON (lower(sku)) row_num, name, sku, description
            FROM {STAGING}
            ORDER BY lower(sku), row_num DESC
        ) dedup
        ON CONFLICT (lower(sku)) DO UPDATE SET
            name = EXCLUDED.name, description = EXCLUDED.description, updated_at = NOW();
    """))
t_a = time.perf_counter() - t0
with engine.connect() as conn:
    cnt_a = conn.execute(text("SELECT COUNT(*) FROM products;")).scalar()
print(f"  UPSERT time: {t_a:.3f}s | Products: {cnt_a:,}")

# ─── BENCHMARK B: Drop GIN → UPSERT → Rebuild ───────────────────────────────
print("\n=== BENCHMARK B: OPTIMIZED — Drop GIN → UPSERT → Rebuild ===")
reset_products()
print(f"  Indexes before: {check_indexes()}")

# Phase 1: Drop GIN
t0 = time.perf_counter()
with engine.connect() as conn:
    with conn.begin():
        conn.execute(text("DROP INDEX IF EXISTS ix_products_name_trgm;"))
        conn.execute(text("DROP INDEX IF EXISTS ix_products_sku_trgm;"))
t_drop = time.perf_counter() - t0
print(f"  Drop GIN indexes: {t_drop:.3f}s | Indexes now: {check_indexes()}")

# Phase 2: UPSERT (no GIN)
t0 = time.perf_counter()
with engine.connect() as conn:
    conn.execute(text("SET work_mem = '128MB';"))
    conn.execute(text(f"""
        INSERT INTO products (sku, name, description, active, created_at, updated_at)
        SELECT sku, name, description, TRUE, NOW(), NOW()
        FROM (
            SELECT DISTINCT ON (lower(sku)) row_num, name, sku, description
            FROM {STAGING}
            ORDER BY lower(sku), row_num DESC
        ) dedup
        ON CONFLICT (lower(sku)) DO UPDATE SET
            name = EXCLUDED.name, description = EXCLUDED.description, updated_at = NOW();
    """))
t_upsert = time.perf_counter() - t0
with engine.connect() as conn:
    cnt_b = conn.execute(text("SELECT COUNT(*) FROM products;")).scalar()
print(f"  UPSERT time: {t_upsert:.3f}s | Products: {cnt_b:,}")

# Phase 3: Rebuild GIN
t0 = time.perf_counter()
with engine.connect() as conn:
    with conn.begin():
        conn.execute(text("CREATE INDEX ix_products_name_trgm ON products USING gin (name gin_trgm_ops);"))
        conn.execute(text("CREATE INDEX ix_products_sku_trgm ON products USING gin (sku gin_trgm_ops);"))
t_rebuild = time.perf_counter() - t0
print(f"  Rebuild GIN: {t_rebuild:.3f}s | Indexes after: {check_indexes()}")

t_b_total = t_drop + t_upsert + t_rebuild
print(f"\n  TOTAL (drop+upsert+rebuild): {t_b_total:.3f}s")

# ─── RESULTS ─────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("BENCHMARK COMPARISON")
print("="*60)
print(f"  A. Current (GIN inline):          {t_a:.3f}s")
print(f"  B. Optimized (drop+upsert+build): {t_b_total:.3f}s")
print(f"     ↳ drop:    {t_drop:.3f}s")
print(f"     ↳ upsert:  {t_upsert:.3f}s")
print(f"     ↳ rebuild: {t_rebuild:.3f}s")
speedup = t_a / t_b_total
print(f"\n  Speedup: {speedup:.2f}x")
print(f"  Saved:   {t_a - t_b_total:.3f}s locally")
print(f"\n  On Render (scaling factor ~11x slower):")
print(f"  A. Current on Render:    ~{t_a * 11:.0f}s")
print(f"  B. Optimized on Render:  ~{t_b_total * 11:.0f}s")

# ─── Cleanup ─────────────────────────────────────────────────────────────────
with engine.connect() as conn:
    with conn.begin():
        conn.execute(text(f"DROP TABLE IF EXISTS {STAGING};"))
print("\nCleanup done.")
