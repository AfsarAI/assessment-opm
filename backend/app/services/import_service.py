import os
import csv
import io
import json
import logging
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

                        # Report progress (scaled between 15% and 60%)
                        progress_pct = int(15 + (processed_rows / total_rows) * 45) if total_rows > 0 else 50
                        publish_progress(
                            r,
                            job_id_str,
                            "VALIDATING",
                            progress_pct,
                            processed_rows,
                            total_rows,
                            successful_rows,
                            failed_rows,
                            f"Validated and staged {processed_rows:,} / {total_rows:,} rows...",
                        )

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

        # 5. Stage 3: In-Database Deduplication with optimized work_mem and sequential chunking
        publish_progress(
            r, job_id_str, "IMPORTING", 65, processed_rows, total_rows, successful_rows, failed_rows, "Deduplicating duplicate SKUs in database..."
        )

        with sync_engine.connect() as conn:
            with conn.begin():
                # Elevate work_mem for this transaction so the 500K-row sort runs in-memory instead of spilling to disk
                conn.execute(text("SET LOCAL work_mem = '64MB';"))
                # Deduplicate: later occurrence (higher row_num) replaces earlier occurrence
                conn.execute(
                    text(
                        f"""
                        CREATE UNLOGGED TABLE {dedup_table} AS
                        SELECT DISTINCT ON (lower(sku))
                            row_num, name, sku, description
                        FROM {staging_table}
                        ORDER BY lower(sku), row_num DESC;
                        """
                    )
                )
                # Add sequential serial column for fast indexed chunking
                conn.execute(text(f"ALTER TABLE {dedup_table} ADD COLUMN chunk_id SERIAL PRIMARY KEY;"))
                # Drop staging table immediately to reclaim storage
                conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))

        # 6. Stage 4: Chunked Set-Based UPSERT into products table with continuous live telemetry
        with sync_engine.connect() as conn:
            res = conn.execute(text(f"SELECT COUNT(*), COALESCE(MAX(chunk_id), 0) FROM {dedup_table};")).fetchone()
            dedup_count = res[0] if res else 0
            max_chunk_id = res[1] if res else 0

        logger.info(f"Deduplicated to {dedup_count:,} unique products. Starting chunked upsert across {max_chunk_id} IDs...")

        UPSERT_CHUNK_SIZE = 25000
        num_batches = max(1, (max_chunk_id + UPSERT_CHUNK_SIZE - 1) // UPSERT_CHUNK_SIZE)
        current_batch = 0

        for b_start in range(1, max_chunk_id + 1, UPSERT_CHUNK_SIZE):
            current_batch += 1
            b_end = b_start + UPSERT_CHUNK_SIZE

            with sync_engine.connect() as batch_conn:
                with batch_conn.begin():
                    # Notice: 'active' is intentionally excluded from DO UPDATE SET, preserving existing status!
                    batch_conn.execute(
                        text(
                            f"""
                            INSERT INTO products (sku, name, description, active, created_at, updated_at)
                            SELECT sku, name, description, TRUE, NOW(), NOW()
                            FROM {dedup_table}
                            WHERE chunk_id >= :b_start AND chunk_id < :b_end
                            ON CONFLICT (lower(sku)) DO UPDATE SET
                                name = EXCLUDED.name,
                                description = EXCLUDED.description,
                                updated_at = NOW();
                            """
                        ),
                        {"b_start": b_start, "b_end": b_end},
                    )

            # Continuous live progress telemetry scaled between 70% and 98%
            current_processed_count = min(current_batch * UPSERT_CHUNK_SIZE, dedup_count)
            progress_pct = int(70 + (current_batch / num_batches) * 28)
            stage_msg = f"Merging products into catalogue ({current_processed_count:,} / {dedup_count:,})..."

            publish_progress(
                r,
                job_id_str,
                "IMPORTING",
                progress_pct,
                processed_rows,
                total_rows,
                successful_rows,
                failed_rows,
                stage_msg,
            )

            # Sync intermediate progress to PostgreSQL database so refreshing the page never shows stale state
            if current_batch % 2 == 0 or current_batch == num_batches:
                try:
                    with get_sync_db() as db:
                        db.execute(
                            text(
                                """
                                UPDATE import_jobs
                                SET progress = :progress,
                                    stage_message = :stage_msg,
                                    processed_rows = :processed,
                                    successful_rows = :successful
                                WHERE id = :id
                                """
                            ),
                            {
                                "id": job_id_str,
                                "progress": progress_pct,
                                "stage_msg": stage_msg,
                                "processed": processed_rows,
                                "successful": successful_rows,
                            },
                        )
                except Exception:
                    pass

        # Cleanup dedup table
        with sync_engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"DROP TABLE IF EXISTS {dedup_table};"))

        # 7. Finalize Job
        final_status = "COMPLETED_WITH_ERRORS" if failed_rows > 0 else "COMPLETED"
        with get_sync_db() as db:
            db.execute(
                text(
                    """
                    UPDATE import_jobs
                    SET status = :status,
                        progress = 100,
                        processed_rows = :processed,
                        successful_rows = :successful,
                        failed_rows = :failed,
                        stage_message = 'Import complete',
                        completed_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": job_id_str,
                    "status": final_status,
                    "processed": processed_rows,
                    "successful": successful_rows,
                    "failed": failed_rows,
                },
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
                    conn.execute(text(f"DROP TABLE IF EXISTS {dedup_table};"))
        except Exception:
            pass

        # Update import_jobs as FAILED
        with get_sync_db() as db:
            db.execute(
                text(
                    """
                    UPDATE import_jobs
                    SET status = 'FAILED',
                        stage_message = 'Import failed',
                        error_message = :err,
                        completed_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": job_id_str, "err": str(e)},
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
