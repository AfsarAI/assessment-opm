import os
import csv
import io
import json
import logging
import threading
import time
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
    """Synchronously updates the authoritative import_jobs state in PostgreSQL with retry on connection drop."""
    for attempt in range(2):
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
                return
        except Exception as e:
            if attempt == 0:
                logger.warning(f"Retrying update_job_db for {import_id_str} after disposing stale connection pool: {e}")
                try:
                    sync_engine.dispose()
                except Exception:
                    pass
                time.sleep(0.5)
            else:
                logger.error(f"Failed to update import_jobs database record for {import_id_str}: {e}")


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
    staged_rows = 0
    successful_rows = 0
    failed_rows = 0
    total_rows = 0
    error_records_to_insert = []
    MAX_ERRORS_STORED = 1000
    gin_indexes_exist = {"name_trgm": False, "sku_trgm": False}

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

        # 3. Create unlogged staging table with pre-normalized sku_lower column
        with sync_engine.connect() as raw_conn:
            with raw_conn.begin():
                raw_conn.execute(
                    text(
                        f"""
                        CREATE UNLOGGED TABLE {staging_table} (
                            row_num INT,
                            sku VARCHAR(100),
                            name VARCHAR(255),
                            description TEXT,
                            sku_lower VARCHAR(100)
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

            # Connect raw psycopg connection for streaming COPY
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

                    batch_valid.append((row_idx, sku, name, description, sku.lower()))

                    if len(batch_valid) >= chunk_size:
                        tsv_buffer = io.StringIO()
                        for r_num, r_sku, r_name, r_desc, r_low in batch_valid:
                            esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            esc_low = r_low.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            tsv_buffer.write(f"{r_num}\t{esc_sku}\t{esc_name}\t{esc_desc}\t{esc_low}\n")
                        tsv_buffer.seek(0)

                        with raw_cursor.copy(f"COPY {staging_table} (row_num, sku, name, description, sku_lower) FROM STDIN") as copy:
                            copy.write(tsv_buffer.getvalue())
                        raw_db_conn.commit()

                        staged_rows += len(batch_valid)
                        batch_valid.clear()

                        # Progress scaled between 15% and 60%
                        progress_pct = int(15 + (processed_rows / total_rows) * 45) if total_rows > 0 else 50
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
                    for r_num, r_sku, r_name, r_desc, r_low in batch_valid:
                        esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        esc_low = r_low.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        tsv_buffer.write(f"{r_num}\t{esc_sku}\t{esc_name}\t{esc_desc}\t{esc_low}\n")
                    tsv_buffer.seek(0)

                    with raw_cursor.copy(f"COPY {staging_table} (row_num, sku, name, description, sku_lower) FROM STDIN") as copy:
                        copy.write(tsv_buffer.getvalue())
                    raw_db_conn.commit()
                    staged_rows += len(batch_valid)
                    batch_valid.clear()

            finally:
                raw_cursor.close()
                raw_db_conn.close()

        # Insert buffered validation errors if any
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

        # 5. In-Database Indexing & Deterministic Deduplication
        stage_msg = "Indexing staged records for zero-memory deduplication..."
        update_job_db(job_id_str, "IMPORTING", 62, stage_msg, processed_rows, successful_rows, failed_rows)
        publish_progress(r, job_id_str, "IMPORTING", 62, processed_rows, total_rows, successful_rows, failed_rows, stage_msg)

        # 5a. Create composite index on staging table for streaming DISTINCT ON (avoids quicksort memory spike)
        with sync_engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"CREATE INDEX idx_{clean_id}_dedup ON {staging_table} (sku_lower, row_num DESC);"))

        # 5b. Create unlogged deduplication table (latest row_num wins, case-insensitive)
        stage_msg = "Deduplicating case-insensitive SKUs (latest row winning)..."
        update_job_db(job_id_str, "IMPORTING", 65, stage_msg, processed_rows, successful_rows, failed_rows)
        publish_progress(r, job_id_str, "IMPORTING", 65, processed_rows, total_rows, successful_rows, failed_rows, stage_msg)

        with sync_engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text(
                        f"""
                        CREATE UNLOGGED TABLE {dedup_table} AS
                        SELECT DISTINCT ON (sku_lower)
                            row_num, sku, name, description, sku_lower
                        FROM {staging_table}
                        ORDER BY sku_lower, row_num DESC;
                        """
                    )
                )
                conn.execute(text(f"ALTER TABLE {dedup_table} ADD PRIMARY KEY (row_num);"))
                # Staging table is no longer needed; drop immediately to reclaim disk space
                conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))

        # 5c. Get range and count from deduplication table
        with sync_engine.connect() as conn:
            row = conn.execute(text(f"SELECT MIN(row_num), MAX(row_num), COUNT(*) FROM {dedup_table};")).fetchone()
            min_row_num = row[0] or 0
            max_row_num = row[1] or 0
            unique_product_count = row[2] or 0

        logger.info(f"Deduplication completed for {job_id_str}: {unique_product_count:,} unique SKUs from {staged_rows:,} staged rows")

        # 6. Drop GIN trigram indexes before catalogue merge to prevent per-row GIN write amplification
        stage_msg = "Optimizing search indexes for catalogue merge..."
        update_job_db(job_id_str, "IMPORTING", 68, stage_msg, processed_rows, successful_rows, failed_rows)
        publish_progress(r, job_id_str, "IMPORTING", 68, processed_rows, total_rows, successful_rows, failed_rows, stage_msg)

        with sync_engine.connect() as conn:
            with conn.begin():
                result = conn.execute(text("""
                    SELECT indexname FROM pg_indexes
                    WHERE tablename = 'products'
                    AND indexname IN ('ix_products_name_trgm', 'ix_products_sku_trgm');
                """))
                for r_idx in result.fetchall():
                    if "name_trgm" in r_idx[0]:
                        gin_indexes_exist["name_trgm"] = True
                    if "sku_trgm" in r_idx[0]:
                        gin_indexes_exist["sku_trgm"] = True

                if gin_indexes_exist["name_trgm"]:
                    conn.execute(text("DROP INDEX IF EXISTS ix_products_name_trgm;"))
                    logger.info("Dropped ix_products_name_trgm for bulk import")
                if gin_indexes_exist["sku_trgm"]:
                    conn.execute(text("DROP INDEX IF EXISTS ix_products_sku_trgm;"))
                    logger.info("Dropped ix_products_sku_trgm for bulk import")

        # 7. Range-Chunked Catalogue UPSERT
        # Executing in bounded 50,000-row chunks prevents:
        # - Linux kernel OOM kills on Render Free Tier (each chunk requires <5 MB RAM)
        # - Reverse proxy TCP idle drops (each chunk completes in ~1.5s, sending fresh query results)
        # - Long lock contention
        # active=false is preserved: ON CONFLICT does NOT overwrite the active column.
        upsert_chunk_size = 50000
        current_id = min_row_num
        total_upserted = 0
        chunk_num = 0
        total_chunks = ((max_row_num - min_row_num) // upsert_chunk_size) + 1 if unique_product_count > 0 else 1

        upsert_error = None
        try:
            while current_id <= max_row_num and unique_product_count > 0:
                chunk_num += 1
                next_id = current_id + upsert_chunk_size

                with sync_engine.connect() as conn:
                    with conn.begin():
                        res = conn.execute(
                            text(
                                f"""
                                INSERT INTO products (sku, name, description, active, created_at, updated_at)
                                SELECT sku, name, description, TRUE, NOW(), NOW()
                                FROM {dedup_table}
                                WHERE row_num >= :start_id AND row_num < :end_id
                                ON CONFLICT (lower(sku)) DO UPDATE SET
                                    name = EXCLUDED.name,
                                    description = EXCLUDED.description,
                                    updated_at = NOW();
                                """
                            ),
                            {"start_id": current_id, "end_id": next_id},
                        )
                        chunk_upserted = res.rowcount
                        total_upserted += chunk_upserted

                current_id = next_id

                # Progress scaled between 70% and 90%
                pct = int(70 + (total_upserted / unique_product_count) * 20) if unique_product_count > 0 else 85
                pct = min(90, max(70, pct))
                stage_msg = f"Merging into catalogue: {total_upserted:,} / {unique_product_count:,} products ({pct}%)..."
                publish_progress(
                    r,
                    job_id_str,
                    "IMPORTING",
                    pct,
                    processed_rows,
                    total_rows,
                    total_upserted,
                    failed_rows,
                    stage_msg,
                )

            # Drop dedup table upon successful merge
            with sync_engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(f"DROP TABLE IF EXISTS {dedup_table};"))

        except Exception as e:
            upsert_error = e

        # 8. Rebuild GIN trgm search indexes in a single sequential scan
        try:
            stage_msg = "Rebuilding search indexes..."
            update_job_db(job_id_str, "IMPORTING", 92, stage_msg, processed_rows, total_upserted, failed_rows)
            publish_progress(r, job_id_str, "IMPORTING", 92, processed_rows, total_rows, total_upserted, failed_rows, stage_msg)

            with sync_engine.connect() as conn:
                with conn.begin():
                    if gin_indexes_exist["name_trgm"]:
                        conn.execute(text("""
                            CREATE INDEX IF NOT EXISTS ix_products_name_trgm ON products
                            USING gin (name gin_trgm_ops);
                        """))
                        logger.info("Rebuilt ix_products_name_trgm")
                    if gin_indexes_exist["sku_trgm"]:
                        conn.execute(text("""
                            CREATE INDEX IF NOT EXISTS ix_products_sku_trgm ON products
                            USING gin (sku gin_trgm_ops);
                        """))
                        logger.info("Rebuilt ix_products_sku_trgm")
        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild GIN indexes: {rebuild_err}")

        # If upsert failed, re-raise error
        if upsert_error is not None:
            raise upsert_error

        # 9. Finalize Job: only committed rows are reported as succeeded
        successful_rows = total_upserted
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
        # Clean up temporary tables
        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))
                    conn.execute(text(f"DROP TABLE IF EXISTS {dedup_table};"))
        except Exception:
            pass

        # Restore GIN indexes if they were dropped before failure
        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    if gin_indexes_exist.get("name_trgm"):
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_name_trgm ON products USING gin (name gin_trgm_ops);"))
                    if gin_indexes_exist.get("sku_trgm"):
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_sku_trgm ON products USING gin (sku gin_trgm_ops);"))
        except Exception:
            pass

        # Update import_jobs as FAILED in PostgreSQL
        # Do NOT report uncommitted rows as succeeded
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
        # Cleanup uploaded file from disk (only if inside upload_dir or tmp)
        if os.path.exists(file_path):
            abs_upload = os.path.abspath(settings.UPLOAD_DIR)
            abs_file = os.path.abspath(file_path)
            if abs_file.startswith(abs_upload) or "/tmp" in abs_file:
                try:
                    os.remove(file_path)
                except Exception:
                    pass
