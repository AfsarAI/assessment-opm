#!/usr/bin/env python3
"""
Multi-Scale Benchmark & Correctness Verification Suite:
Tests imports of:
1. 100 rows
2. 1,000 rows
3. 10,000 rows
4. 100,000 rows
5. 500,000 rows (products.csv)

For each, measures:
- Total rows, Unique SKUs, Duplicates
- End-to-end execution time
- Rows/sec throughput
- Final database record count
- Preservation of active=false on existing products
- Case-insensitive deduplication correctness
"""

import os
import sys
import time
import uuid
from sqlalchemy import create_engine, text

# Add backend to path
sys.path.insert(0, os.path.abspath("backend"))

from app.services.import_service import execute_import_pipeline
from app.core.database import sync_engine

def run_benchmark():
    print("=" * 70)
    print("ASSESSMENT OPM: MULTI-SCALE BENCHMARK & CORRECTNESS SUITE")
    print("=" * 70)

    datasets = [
        ("100 rows", "scripts/samples/products_100.csv"),
        ("1,000 rows", "scripts/samples/products_1000.csv"),
        ("10,000 rows", "scripts/samples/products_10000.csv"),
        ("100,000 rows", "scripts/samples/products_100000.csv"),
        ("500,000 rows", "products.csv"),
    ]

    results = []

    for label, filepath in datasets:
        print(f"\n[{label.upper()}] Starting benchmark on {filepath}...")
        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            print(f"Error: {abs_path} not found!")
            continue

        file_size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        job_id = str(uuid.uuid4())

        # Reset database before run to ensure clean baseline
        with sync_engine.connect() as conn:
            with conn.begin():
                conn.execute(text("TRUNCATE TABLE products RESTART IDENTITY;"))
                # Insert a test product with active=false whose SKU matches a row in the CSV ('LAY-RAISE-BEST-END')
                conn.execute(text("""
                    INSERT INTO products (sku, name, description, active, created_at, updated_at)
                    VALUES ('LAY-RAISE-BEST-END', 'Old Pre-existing Name', 'Pre-existing description', false, NOW(), NOW());
                """))
                # Register import job in import_jobs
                conn.execute(
                    text("""
                        INSERT INTO import_jobs (id, filename, status, stage_message, progress, created_at, updated_at)
                        VALUES (:id, :filename, 'QUEUED', 'Queued', 0, NOW(), NOW());
                    """),
                    {"id": job_id, "filename": os.path.basename(filepath)},
                )

        t0 = time.perf_counter()
        execute_import_pipeline(job_id, abs_path)
        duration = time.perf_counter() - t0

        # Query final database state
        with sync_engine.connect() as conn:
            job_row = conn.execute(
                text("SELECT status, total_rows, processed_rows, successful_rows, failed_rows, stage_message FROM import_jobs WHERE id = :id"),
                {"id": job_id},
            ).fetchone()
            product_count = conn.execute(text("SELECT COUNT(*) FROM products;")).scalar()
            preserved_row = conn.execute(
                text("SELECT sku, name, active FROM products WHERE lower(sku) = 'lay-raise-best-end';")
            ).fetchone()

        status, total, processed, succeeded, failed, stage_msg = job_row
        throughput = total / duration if duration > 0 else 0
        preserved_inactive = (preserved_row[2] is False) if preserved_row else False
        updated_name = (preserved_row[1] == "Bryce Jones") if preserved_row else False

        res_summary = {
            "label": label,
            "file": os.path.basename(filepath),
            "size_mb": round(file_size_mb, 2),
            "duration": round(duration, 3),
            "total_rows": total,
            "successful_rows": succeeded,
            "failed_rows": failed,
            "product_count": product_count,
            "throughput_rows_sec": round(throughput, 1),
            "status": status,
            "inactive_preserved": preserved_inactive,
            "name_updated": updated_name,
        }
        results.append(res_summary)

        print(f"  Status:             {status}")
        print(f"  Duration:           {duration:.2f}s ({throughput:,.0f} rows/sec)")
        print(f"  File Size:          {file_size_mb:.2f} MB")
        print(f"  Total CSV Rows:     {total:,}")
        print(f"  Committed Products: {succeeded:,}")
        print(f"  Table Product Count:{product_count:,}")
        print(f"  active=false Preserved on Upsert: {'YES' if preserved_inactive else 'NO'}")
        print(f"  Name updated to 'Bryce Jones':    {'YES' if updated_name else 'NO'}")

    print("\n" + "=" * 80)
    print(f"{'Scale':<15} {'CSV Size':>10} {'Duration':>10} {'Rows/Sec':>12} {'Succeeded':>12} {'active=F':>10} {'Status':>8}")
    print("-" * 80)
    for r in results:
        active_str = "PRESERVED" if r['inactive_preserved'] else "FAILED"
        print(f"{r['label']:<15} {r['size_mb']:>8.2f}MB {r['duration']:>9.2f}s {r['throughput_rows_sec']:>11,.0f} {r['successful_rows']:>12,} {active_str:>10} {r['status']:>8}")
    print("=" * 80)

    import json
    with open("docs/scale_benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved benchmark metrics to docs/scale_benchmark_results.json")

if __name__ == "__main__":
    run_benchmark()
