#!/usr/bin/env python3
"""
FORENSIC PROFILER: Measures every individual stage of the 500K import pipeline.
Runs EXPLAIN ANALYZE on each SQL operation separately against the real products.csv dataset.
"""

import csv
import io
import os
import sys
import time
import json
import logging

# Use local postgres (Docker) for forensic profiling
DATABASE_URL = "postgresql+psycopg://opm_user:opm_password@localhost:5432/opm_db"
SYNC_ENGINE_URL = "postgresql+psycopg://opm_user:opm_password@localhost:5432/opm_db"

from sqlalchemy import create_engine, text

engine = create_engine(SYNC_ENGINE_URL, echo=False)

CSV_FILE = "/home/afsarai/assessment-opm/products.csv"
JOB_ID = "forensic_test_01"
STAGING = "forensic_staging"

timings = {}

def t(label, fn):
    """Time a function, print result, store in timings dict."""
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    timings[label] = elapsed
    print(f"  [{elapsed:8.3f}s] {label}")
    return result

def run():
    print("=" * 65)
    print("FORENSIC IMPORT PIPELINE PROFILER")
    print("Dataset: products.csv (500K rows, ~86 MB)")
    print("=" * 65)

    # ── STAGE 0: Count rows ──────────────────────────────────────
    print("\n[0] CSV Parsing & Row Counting")
    def count_rows():
        with open(CSV_FILE, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)
            return sum(1 for _ in reader)

    total_rows = t("CSV row count (csv.reader)", count_rows)
    print(f"      → {total_rows:,} rows")

    # ── STAGE 1: Drop/Create staging table ──────────────────────
    print("\n[1] Staging Table Creation")
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(f"DROP TABLE IF EXISTS {STAGING};"))

    def create_staging():
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"""
                    CREATE UNLOGGED TABLE {STAGING} (
                        row_num INT,
                        name VARCHAR(255),
                        sku VARCHAR(100),
                        description TEXT
                    );
                """))

    t("CREATE UNLOGGED TABLE", create_staging)

    # ── STAGE 2: PostgreSQL COPY ─────────────────────────────────
    print("\n[2] PostgreSQL COPY (streaming validation + binary insert)")
    batch_valid = []
    chunk_size = 25000
    total_copied = 0
    copy_start = time.perf_counter()

    raw_db_conn = engine.raw_connection()
    raw_cursor = raw_db_conn.cursor()

    with open(CSV_FILE, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row_idx, row in enumerate(reader, start=1):
            if len(row) < 3:
                continue
            name, sku = row[0].strip(), row[1].strip()
            description = row[2].strip() if len(row) > 2 else ""
            if not name or not sku:
                continue
            batch_valid.append((row_idx, name, sku, description))
            if len(batch_valid) >= chunk_size:
                tsv_buffer = io.StringIO()
                for r_num, r_name, r_sku, r_desc in batch_valid:
                    esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                    esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                    esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                    tsv_buffer.write(f"{r_num}\t{esc_name}\t{esc_sku}\t{esc_desc}\n")
                tsv_buffer.seek(0)
                with raw_cursor.copy(f"COPY {STAGING} (row_num, name, sku, description) FROM STDIN") as copy:
                    copy.write(tsv_buffer.getvalue())
                raw_db_conn.commit()
                total_copied += len(batch_valid)
                batch_valid.clear()

    if batch_valid:
        tsv_buffer = io.StringIO()
        for r_num, r_name, r_sku, r_desc in batch_valid:
            esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
            esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
            esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
            tsv_buffer.write(f"{r_num}\t{esc_name}\t{esc_sku}\t{esc_desc}\n")
        tsv_buffer.seek(0)
        with raw_cursor.copy(f"COPY {STAGING} (row_num, name, sku, description) FROM STDIN") as copy:
            copy.write(tsv_buffer.getvalue())
        raw_db_conn.commit()
        total_copied += len(batch_valid)

    raw_cursor.close()
    raw_db_conn.close()

    copy_time = time.perf_counter() - copy_start
    timings["COPY + parse + TSV escape"] = copy_time
    print(f"  [{copy_time:8.3f}s] COPY + parse + TSV escape → {total_copied:,} rows @ {total_copied/copy_time:.0f} rows/s")

    # ── STAGE 3: Staging table stats ────────────────────────────
    print("\n[3] Staging Table Size Analysis")
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            SELECT 
                COUNT(*) AS count,
                pg_size_pretty(pg_total_relation_size('{STAGING}')) AS size
            FROM {STAGING};
        """)).fetchone()
    print(f"      → {row[0]:,} rows | {row[1]}")

    # ── STAGE 4: Index benchmarks on staging ────────────────────
    print("\n[4] Staging Index Creation Benchmarks")

    def create_index_lower_sku():
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"DROP INDEX IF EXISTS idx_staging_lower_sku;"))
                conn.execute(text(f"CREATE INDEX idx_staging_lower_sku ON {STAGING} (lower(sku));"))

    def create_index_composite():
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"DROP INDEX IF EXISTS idx_staging_composite;"))
                conn.execute(text(f"CREATE INDEX idx_staging_composite ON {STAGING} (lower(sku), row_num DESC);"))

    t("CREATE INDEX lower(sku)", create_index_lower_sku)
    t("CREATE INDEX lower(sku), row_num DESC (composite)", create_index_composite)

    # ── STAGE 5: DISTINCT ON deduplication EXPLAIN ANALYZE ──────
    print("\n[5] Deduplication Strategy Benchmarks (DISTINCT ON)")
    def explain_distinct_on():
        with engine.connect() as conn:
            conn.execute(text("SET work_mem = '128MB';"))
            result = conn.execute(text(f"""
                EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
                SELECT DISTINCT ON (lower(sku)) row_num, name, sku, description
                FROM {STAGING}
                ORDER BY lower(sku), row_num DESC;
            """))
            return result.fetchall()

    def time_distinct_on():
        with engine.connect() as conn:
            conn.execute(text("SET work_mem = '128MB';"))
            result = conn.execute(text(f"""
                SELECT DISTINCT ON (lower(sku)) row_num, name, sku, description
                FROM {STAGING}
                ORDER BY lower(sku), row_num DESC;
            """))
            return result.fetchall()

    explain_rows = t("EXPLAIN ANALYZE DISTINCT ON (with index + 128MB work_mem)", explain_distinct_on)
    print("      EXPLAIN output:")
    for row in explain_rows:
        print(f"        {row[0]}")

    dedup_rows = t("DISTINCT ON actual execution (128MB work_mem)", time_distinct_on)
    unique_count = len(dedup_rows)
    print(f"      → {unique_count:,} unique SKUs")

    # ── STAGE 6: DISTINCT ON without index ──────────────────────
    print("\n[6] DISTINCT ON without index (drop composite, keep lower_sku)")
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(f"DROP INDEX IF EXISTS idx_staging_composite;"))

    def time_distinct_on_no_composite():
        with engine.connect() as conn:
            conn.execute(text("SET work_mem = '128MB';"))
            result = conn.execute(text(f"""
                SELECT DISTINCT ON (lower(sku)) row_num, name, sku, description
                FROM {STAGING}
                ORDER BY lower(sku), row_num DESC;
            """))
            return len(result.fetchall())

    t("DISTINCT ON without composite index", time_distinct_on_no_composite)

    # ── STAGE 7: ROW_NUMBER() deduplication alternative ─────────
    print("\n[7] ROW_NUMBER() deduplication strategy (alternative)")
    def time_row_number_dedup():
        with engine.connect() as conn:
            conn.execute(text("SET work_mem = '128MB';"))
            result = conn.execute(text(f"""
                SELECT row_num, name, sku, description FROM (
                    SELECT row_num, name, sku, description,
                           ROW_NUMBER() OVER (PARTITION BY lower(sku) ORDER BY row_num DESC) AS rn
                    FROM {STAGING}
                ) ranked WHERE rn = 1;
            """))
            return len(result.fetchall())

    t("ROW_NUMBER() dedup (128MB work_mem)", time_row_number_dedup)

    # ── STAGE 8: Full pipeline UPSERT (empty products table) ────
    print("\n[8] Full UPSERT Pipeline (products table EMPTY baseline)")
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("TRUNCATE products;"))

    def upsert_empty():
        with engine.connect() as conn:
            conn.execute(text("SET work_mem = '128MB';"))
            result = conn.execute(text(f"""
                INSERT INTO products (sku, name, description, active, created_at, updated_at)
                SELECT sku, name, description, TRUE, NOW(), NOW()
                FROM (
                    SELECT DISTINCT ON (lower(sku))
                        row_num, name, sku, description
                    FROM {STAGING}
                    ORDER BY lower(sku), row_num DESC
                ) dedup
                ON CONFLICT (lower(sku)) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    updated_at = NOW();
            """))
            return result.rowcount

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET work_mem = '128MB';"))

    upsert_count = t("UPSERT into EMPTY products (all INSERTs)", upsert_empty)

    with engine.connect() as conn:
        final_count = conn.execute(text("SELECT COUNT(*) FROM products;")).scalar()
    print(f"      → {final_count:,} products inserted | rowcount={upsert_count}")

    # ── STAGE 9: UPSERT with active=false preservation test ─────
    print("\n[9] UPSERT: active=false preservation logic")
    # Change a few products to active=false then re-import to verify preservation
    with engine.connect() as conn:
        with conn.begin():
            # Mark 1000 random products as inactive
            conn.execute(text("UPDATE products SET active = false WHERE id IN (SELECT id FROM products LIMIT 1000);"))

    def upsert_with_conflicts():
        with engine.connect() as conn:
            conn.execute(text("SET work_mem = '128MB';"))
            conn.execute(text(f"""
                INSERT INTO products (sku, name, description, active, created_at, updated_at)
                SELECT sku, name, description, TRUE, NOW(), NOW()
                FROM (
                    SELECT DISTINCT ON (lower(sku))
                        row_num, name, sku, description
                    FROM {STAGING}
                    ORDER BY lower(sku), row_num DESC
                ) dedup
                ON CONFLICT (lower(sku)) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    updated_at = NOW();
            """))

    t("UPSERT into FULL products (all UPDATEs + active preservation)", upsert_with_conflicts)

    # Verify active=false preserved
    with engine.connect() as conn:
        inactive = conn.execute(text("SELECT COUNT(*) FROM products WHERE active = false;")).scalar()
    print(f"      → active=false preserved for {inactive:,} products")

    # ── STAGE 10: EXPLAIN ANALYZE on UPSERT (full products) ─────
    print("\n[10] EXPLAIN ANALYZE on UPSERT (full products table)")
    with engine.connect() as conn:
        result = conn.execute(text(f"""
            EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT TEXT)
            INSERT INTO products (sku, name, description, active, created_at, updated_at)
            SELECT sku, name, description, TRUE, NOW(), NOW()
            FROM (
                SELECT DISTINCT ON (lower(sku))
                    row_num, name, sku, description
                FROM {STAGING}
                ORDER BY lower(sku), row_num DESC
            ) dedup
            ON CONFLICT (lower(sku)) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                updated_at = NOW();
        """))
        print("  EXPLAIN output (key lines):")
        for row in result.fetchall():
            line = row[0]
            # Print important lines
            if any(k in line for k in ["time=", "Sort", "Disk", "Buffers", "Planning", "Execution", "Unique", "Sort Method", "Merge", "Hash"]):
                print(f"    {line}")

    # ── STAGE 11: WITH conditional update (only if changed) ─────
    print("\n[11] UPSERT with WHERE clause (skip unchanged rows)")
    t0_cond = time.perf_counter()
    with engine.connect() as conn:
        conn.execute(text("SET work_mem = '128MB';"))
        conn.execute(text(f"""
            INSERT INTO products (sku, name, description, active, created_at, updated_at)
            SELECT sku, name, description, TRUE, NOW(), NOW()
            FROM (
                SELECT DISTINCT ON (lower(sku))
                    row_num, name, sku, description
                FROM {STAGING}
                ORDER BY lower(sku), row_num DESC
            ) dedup
            ON CONFLICT (lower(sku)) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                updated_at = NOW()
            WHERE products.name IS DISTINCT FROM EXCLUDED.name
               OR products.description IS DISTINCT FROM EXCLUDED.description;
        """))
    cond_time = time.perf_counter() - t0_cond
    timings["UPSERT with WHERE skip unchanged"] = cond_time
    print(f"  [{cond_time:8.3f}s] UPSERT WHERE skip unchanged")

    # ── STAGE 12: sku_normalized column approach ─────────────────
    print("\n[12] Normalized SKU column architecture benchmark")
    print("     Creating test table with sku_normalized column...")
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("DROP TABLE IF EXISTS products_normalized_test;"))
            conn.execute(text("""
                CREATE UNLOGGED TABLE products_normalized_test (
                    id BIGSERIAL PRIMARY KEY,
                    sku VARCHAR(100) NOT NULL,
                    sku_normalized VARCHAR(100) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                );
            """))
            conn.execute(text("""
                CREATE UNIQUE INDEX uq_products_norm_sku ON products_normalized_test (sku_normalized);
            """))

    def upsert_normalized():
        with engine.connect() as conn:
            conn.execute(text("SET work_mem = '128MB';"))
            conn.execute(text(f"""
                INSERT INTO products_normalized_test (sku, sku_normalized, name, description, active, created_at, updated_at)
                SELECT sku, lower(sku), name, description, TRUE, NOW(), NOW()
                FROM (
                    SELECT DISTINCT ON (lower(sku))
                        row_num, name, sku, description
                    FROM {STAGING}
                    ORDER BY lower(sku), row_num DESC
                ) dedup
                ON CONFLICT (sku_normalized) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    updated_at = NOW();
            """))

    t("UPSERT into sku_normalized table (UNLOGGED, fresh)", upsert_normalized)

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("DROP TABLE IF EXISTS products_normalized_test;"))

    # ── STAGE 13: DROP staging table ────────────────────────────
    print("\n[13] Cleanup")
    def drop_staging():
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"DROP TABLE IF EXISTS {STAGING};"))
    t("DROP TABLE staging", drop_staging)

    # ── SUMMARY ─────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("FORENSIC PROFILER RESULTS")
    print("=" * 65)
    print(f"{'Stage':<52} {'Time':>9}")
    print("-" * 65)
    for k, v in timings.items():
        print(f"  {k:<50} {v:>7.3f}s")

    total = sum(timings.values())
    print("-" * 65)
    print(f"  {'TOTAL':50} {total:>7.3f}s")
    print("=" * 65)

    # Save to JSON
    with open("docs/forensic_profiler_results.json", "w") as f:
        json.dump({k: round(v, 4) for k, v in timings.items()}, f, indent=2)
    print("\nResults saved to docs/forensic_profiler_results.json")

if __name__ == "__main__":
    run()
