import os
import csv
import io
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis
from sqlalchemy import text
from app.core.config import settings
from app.core.database import get_sync_db, sync_engine

logger = logging.getLogger("import_service")


def get_redis_client():
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def update_job_db(
    import_id_str: str,
    status: str,
    progress: int,
    stage_message: str,
    processed_rows: int,
    successful_rows: int,
    failed_rows: int,
    error_message: Optional[str] = None,
):
    """Synchronously updates the authoritative import_jobs state in PostgreSQL."""
    try:
        with get_sync_db() as db:
            db.execute(
                text(
                    """
                    UPDATE import_jobs
                    SET status = :status,
                        progress = :progress,
                        stage_message = :stage_msg,
                        processed_rows = :processed,
                        successful_rows = :successful,
                        failed_rows = :failed,
                        error_message = :err,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": import_id_str,
                    "status": status,
                    "progress": progress,
                    "stage_msg": stage_message,
                    "processed": processed_rows,
                    "successful": successful_rows,
                    "failed": failed_rows,
                    "err": error_message,
                },
            )
    except Exception as e:
        logger.warning(f"Failed to update import_jobs database record for {import_id_str}: {e}")


def publish_progress(
    r: redis.Redis,
    import_id: str,
    status: str,
    progress: int,
    processed_rows: int,
    total_rows: int,
    successful_rows: int,
    failed_rows: int,
    stage_message: str,
    error_message: Optional[str] = None,
):
    payload = {
        "import_id": str(import_id),
        "status": status,
        "progress": progress,
        "processed_rows": processed_rows,
        "total_rows": total_rows,
        "successful_rows": successful_rows,
        "failed_rows": failed_rows,
        "stage_message": stage_message,
        "error_message": error_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    channel = f"import_progress:{import_id}"
    try:
        r.publish(channel, json.dumps(payload))
        # Also store the latest state in Redis with 1-hour expiry for instant fetch
        r.setex(f"import_latest:{import_id}", 3600, json.dumps(payload))
    except Exception as e:
        logger.warning(f"Failed to publish progress to Redis for import {import_id}: {e}")


def execute_import_pipeline(job_id_str: str, file_path: str):
    logger.info(f"Starting import pipeline for job {job_id_str} with file {file_path}")
    r = get_redis_client()
    clean_id = job_id_str.replace("-", "")
    staging_table = f"staging_{clean_id}"
    dedup_table = f"dedup_{clean_id}"

    processed_rows = 0
    successful_rows = 0
    failed_rows = 0
    total_rows = 0
    error_records_to_insert = []
    MAX_ERRORS_STORED = 1000

    try:
        # 1. Transition state to PARSING
        with get_sync_db() as db:
            db.execute(
                text(
                    """
                    UPDATE import_jobs
                    SET status = 'PARSING',
                        stage_message = 'Parsing CSV header and validating structure...',
                        started_at = NOW(),
                        progress = 5
                    WHERE id = :id
                    """
                ),
                {"id": job_id_str},
            )
        publish_progress(
            r, job_id_str, "PARSING", 5, 0, 0, 0, 0, "Parsing CSV header and validating structure..."
        )

        # 2. Check file existence & count rows
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File {file_path} not found on server")

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)  # Exclude header
            total_rows = sum(1 for _ in reader)
            total_rows = max(0, total_rows)

        with get_sync_db() as db:
            db.execute(
                text(
                    """
                    UPDATE import_jobs
                    SET total_rows = :total,
                        stage_message = 'Preparing PostgreSQL unlogged staging table...',
                        progress = 10
                    WHERE id = :id
                    """
                ),
                {"id": job_id_str, "total": total_rows},
            )
        publish_progress(
            r, job_id_str, "PARSING", 10, 0, total_rows, 0, 0, "Preparing PostgreSQL unlogged staging table..."
        )

        # 3. Create unlogged staging table using raw DB connection
        with sync_engine.connect() as raw_conn:
            with raw_conn.begin():
                raw_conn.execute(
                    text(
                        f"""
                        CREATE UNLOGGED TABLE {staging_table} (
                            row_num INT,
                            name VARCHAR(255),
                            sku VARCHAR(100),
                            description TEXT
                        );
                        """
                    )
                )

        # 4. Stream parse and COPY into staging table
        publish_progress(
            r, job_id_str, "VALIDATING", 15, 0, total_rows, 0, 0, "Validating and streaming data to staging..."
        )

        chunk_size = 25000
        batch_valid = []
        
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                raise ValueError("CSV file is completely empty.")

            # Validate header
            cleaned_header = [col.strip().lower() for col in header]
            if len(cleaned_header) < 3 or cleaned_header[0] != "name" or cleaned_header[1] != "sku" or cleaned_header[2] != "description":
                raise ValueError(f"Invalid CSV header: expected ['name', 'sku', 'description'], got {header}")

            # Connect raw psycopg connection for COPY command
            raw_db_conn = sync_engine.raw_connection()
            try:
                raw_cursor = raw_db_conn.cursor()

                for row_idx, row in enumerate(reader, start=1):
                    processed_rows += 1
                    
                    if len(row) < 3:
                        failed_rows += 1
                        if len(error_records_to_insert) < MAX_ERRORS_STORED:
                            error_records_to_insert.append((job_id_str, row_idx, "Malformed row: missing columns", ",".join(row)))
                        continue

                    name = row[0].strip()
                    sku = row[1].strip()
                    description = row[2].strip() if len(row) > 2 else ""

                    if not name or not sku:
                        failed_rows += 1
                        if len(error_records_to_insert) < MAX_ERRORS_STORED:
                            msg = "Missing required product name" if not name else "Missing required product SKU"
                            error_records_to_insert.append((job_id_str, row_idx, msg, ",".join(row)))
                        continue

                    batch_valid.append((row_idx, name, sku, description))

                    if len(batch_valid) >= chunk_size:
                        # Flush batch to staging via PostgreSQL COPY
                        tsv_buffer = io.StringIO()
                        for r_num, r_name, r_sku, r_desc in batch_valid:
                            # Sanitize TSV tabs, newlines, backslashes
                            esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            tsv_buffer.write(f"{r_num}\t{esc_name}\t{esc_sku}\t{esc_desc}\n")
                        tsv_buffer.seek(0)

                        # Execute raw COPY
                        with raw_cursor.copy(f"COPY {staging_table} (row_num, name, sku, description) FROM STDIN") as copy:
                            copy.write(tsv_buffer.getvalue())
                        raw_db_conn.commit()

                        successful_rows += len(batch_valid)
                        batch_valid.clear()

                        # Report progress (scaled between 15% and 65%)
                        progress_pct = int(15 + (processed_rows / total_rows) * 50) if total_rows > 0 else 50
                        stage_msg = f"Validated and staged {processed_rows:,} / {total_rows:,} rows..."
                        publish_progress(
                            r,
                            job_id_str,
                            "VALIDATING",
                            progress_pct,
                            processed_rows,
                            total_rows,
                            successful_rows,
                            failed_rows,
                            stage_msg,
                        )
                        if processed_rows % 100000 == 0:
                            update_job_db(job_id_str, "VALIDATING", progress_pct, stage_msg, processed_rows, successful_rows, failed_rows)

                # Flush remaining records
                if batch_valid:
                    tsv_buffer = io.StringIO()
                    for r_num, r_name, r_sku, r_desc in batch_valid:
                        esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        tsv_buffer.write(f"{r_num}\t{esc_name}\t{esc_sku}\t{esc_desc}\n")
                    tsv_buffer.seek(0)

                    with raw_cursor.copy(f"COPY {staging_table} (row_num, name, sku, description) FROM STDIN") as copy:
                        copy.write(tsv_buffer.getvalue())
                    raw_db_conn.commit()
                    successful_rows += len(batch_valid)
                    batch_valid.clear()

            finally:
                raw_cursor.close()
                raw_db_conn.close()

        # Insert buffered errors if any
        if error_records_to_insert:
            with get_sync_db() as db:
                for imp_id, r_num, err_msg, raw_d in error_records_to_insert:
                    db.execute(
                        text(
                            """
                            INSERT INTO import_errors (import_id, row_number, error_message, raw_data, created_at)
                            VALUES (:import_id, :row_num, :error_msg, :raw_data, NOW())
                            """
                        ),
                        {"import_id": imp_id, "row_num": r_num, "error_msg": err_msg, "raw_data": raw_d[:500]},
                    )

        # 5. Stage 3 & 4: Optimized Bulk UPSERT with GIN Index Bypass
        #
        # ROOT CAUSE OF PREVIOUS BOTTLENECK:
        # The products table has 5 indexes: 1 pk, 1 unique btree (lower sku),
        # 1 btree active, 1 btree created_at, and 2 GIN trigram indexes
        # (ix_products_name_trgm, ix_products_sku_trgm).
        # Each row INSERT/UPDATE triggers GIN page writes for both trgm indexes.
        # At 466K rows on Render's slow free-tier I/O, this caused 200+ seconds.
        #
        # THE FIX:
        # 1. DROP the 2 GIN trgm indexes before the UPSERT
        # 2. UPSERT (now only 3 btree indexes maintained → much faster)
        # 3. CREATE the 2 GIN trgm indexes in a single table scan → O(N) not O(N*logN*writes)
        #
        # active=false is preserved because ON CONFLICT does NOT touch the active column.

        # ── 5a. Drop GIN indexes before bulk UPSERT ──────────────────────────
        stage_msg = "Optimizing indexes for bulk import..."
        update_job_db(job_id_str, "IMPORTING", 68, stage_msg, processed_rows, successful_rows, failed_rows)
        publish_progress(r, job_id_str, "IMPORTING", 68, processed_rows, total_rows, successful_rows, failed_rows, stage_msg)

        gin_indexes_exist = {"name_trgm": False, "sku_trgm": False}
        with sync_engine.connect() as conn:
            with conn.begin():
                # Check which indexes exist before dropping
                result = conn.execute(text("""
                    SELECT indexname FROM pg_indexes
                    WHERE tablename = 'products'
                    AND indexname IN ('ix_products_name_trgm', 'ix_products_sku_trgm');
                """))
                for row in result.fetchall():
                    if "name_trgm" in row[0]:
                        gin_indexes_exist["name_trgm"] = True
                    if "sku_trgm" in row[0]:
                        gin_indexes_exist["sku_trgm"] = True

                if gin_indexes_exist["name_trgm"]:
                    conn.execute(text("DROP INDEX IF EXISTS ix_products_name_trgm;"))
                    logger.info("Dropped ix_products_name_trgm for bulk import")
                if gin_indexes_exist["sku_trgm"]:
                    conn.execute(text("DROP INDEX IF EXISTS ix_products_sku_trgm;"))
                    logger.info("Dropped ix_products_sku_trgm for bulk import")

        # ── 5b. Launch heartbeat so SSE stays alive during UPSERT ────────────
        stop_heartbeat = threading.Event()

        def upsert_heartbeat():
            step = 0
            while not stop_heartbeat.wait(2.0):
                step += 1
                hb_progress = min(88, 70 + step * 2)
                hb_msg = f"Merging {total_rows:,} products into catalogue ({step * 2}s elapsed)..."
                publish_progress(
                    r, job_id_str, "IMPORTING", hb_progress, processed_rows, total_rows, successful_rows, failed_rows, hb_msg
                )

        hb_thread = threading.Thread(target=upsert_heartbeat, daemon=True)
        hb_thread.start()

        upsert_error = None
        try:
            # ── 5c. Bulk UPSERT (only btree indexes active now) ──────────────
            stage_msg = "Bulk upserting into products catalogue..."
            update_job_db(job_id_str, "IMPORTING", 70, stage_msg, processed_rows, successful_rows, failed_rows)

            with sync_engine.connect() as conn:
                with conn.begin():
                    # Performance settings for bulk merge:
                    # - work_mem: sort fits in memory (no disk spill for DISTINCT ON)
                    # - synchronous_commit=off: skip WAL fsync (safe - bulk import, FAILED on crash)
                    conn.execute(text("SET LOCAL work_mem = '128MB';"))
                    conn.execute(text("SET LOCAL synchronous_commit = off;"))
                    conn.execute(
                        text(
                            f"""
                            INSERT INTO products (sku, name, description, active, created_at, updated_at)
                            SELECT sku, name, description, TRUE, NOW(), NOW()
                            FROM (
                                SELECT DISTINCT ON (lower(sku))
                                    row_num, name, sku, description
                                FROM {staging_table}
                                ORDER BY lower(sku), row_num DESC
                            ) dedup
                            ON CONFLICT (lower(sku)) DO UPDATE SET
                                name = EXCLUDED.name,
                                description = EXCLUDED.description,
                                updated_at = NOW();
                            """
                        )
                    )
                    # Drop staging table in same transaction for atomicity
                    conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))

        except Exception as e:
            upsert_error = e
        finally:
            stop_heartbeat.set()
            hb_thread.join(timeout=1.0)

        # ── 5d. Rebuild GIN trgm indexes after UPSERT ────────────────────────
        # Even if UPSERT failed, we MUST rebuild the indexes we dropped.
        # A single sequential scan to rebuild is orders of magnitude faster
        # than per-row GIN page writes during bulk insert.
        try:
            stage_msg = "Rebuilding search indexes..."
            update_job_db(job_id_str, "IMPORTING", 90, stage_msg, processed_rows, successful_rows, failed_rows)
            publish_progress(r, job_id_str, "IMPORTING", 90, processed_rows, total_rows, successful_rows, failed_rows, stage_msg)

            with sync_engine.connect() as conn:
                with conn.begin():
                    # Use large maintenance_work_mem for fast GIN index build
                    conn.execute(text("SET LOCAL maintenance_work_mem = '128MB';"))
                    if gin_indexes_exist["name_trgm"]:
                        conn.execute(text("""
                            CREATE INDEX ix_products_name_trgm ON products
                            USING gin (name gin_trgm_ops);
                        """))
                        logger.info("Rebuilt ix_products_name_trgm")
                    if gin_indexes_exist["sku_trgm"]:
                        conn.execute(text("""
                            CREATE INDEX ix_products_sku_trgm ON products
                            USING gin (sku gin_trgm_ops);
                        """))
                        logger.info("Rebuilt ix_products_sku_trgm")
        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild GIN indexes: {rebuild_err}")
            # Non-fatal: search will still work, just slower without trgm

        # Now re-raise UPSERT error if any
        if upsert_error is not None:
            raise upsert_error

        # 6. Finalize Job
        final_status = "COMPLETED_WITH_ERRORS" if failed_rows > 0 else "COMPLETED"
        update_job_db(
            job_id_str,
            final_status,
            100,
            "Import complete",
            processed_rows,
            successful_rows,
            failed_rows,
        )

        publish_progress(
            r,
            job_id_str,
            final_status,
            100,
            processed_rows,
            total_rows,
            successful_rows,
            failed_rows,
            "Import complete",
        )

        logger.info(f"Import {job_id_str} completed successfully: {successful_rows:,} succeeded, {failed_rows:,} failed.")

        # Dispatch import.completed webhook
        try:
            from app.tasks.webhook_tasks import dispatch_webhook_event
            dispatch_webhook_event.delay(
                "import.completed",
                {
                    "import_id": job_id_str,
                    "status": final_status,
                    "total_rows": total_rows,
                    "successful_rows": successful_rows,
                    "failed_rows": failed_rows,
                },
            )
        except Exception:
            pass

    except Exception as e:
        logger.exception(f"Import pipeline failed for job {job_id_str}: {e}")
        # Clean up staging tables if they exist
        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))
        except Exception:
            pass

        # Update import_jobs as FAILED in PostgreSQL
        update_job_db(
            job_id_str,
            "FAILED",
            0,
            "Import failed",
            processed_rows,
            successful_rows,
            failed_rows,
            error_message=str(e),
        )
        publish_progress(
            r,
            job_id_str,
            "FAILED",
            0,
            processed_rows,
            total_rows,
            successful_rows,
            failed_rows,
            f"Import failed: {str(e)}",
            error_message=str(e),
        )

        # Dispatch import.failed webhook
        try:
            from app.tasks.webhook_tasks import dispatch_webhook_event
            dispatch_webhook_event.delay(
                "import.failed",
                {"import_id": job_id_str, "error": str(e)},
            )
        except Exception:
            pass
        raise
    finally:
        # Cleanup uploaded file from disk
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
