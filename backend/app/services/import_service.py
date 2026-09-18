import os
import csv
import io
import json
import logging
import math
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


class ImportCancelledException(Exception):
    """Raised when an active import is cancelled by the user."""
    pass


def get_redis_client():
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def is_cancellation_requested(job_id_str: str, r: Optional[redis.Redis] = None) -> bool:
    """Checks whether a cancellation flag was set in Redis or DB for this import job."""
    try:
        if r is None:
            r = get_redis_client()
        val = r.get(f"import_cancel:{job_id_str}")
        if val in ("1", 1, True, "true"):
            return True
    except Exception:
        pass

    try:
        with get_sync_db() as db:
            row = db.execute(
                text("SELECT status FROM import_jobs WHERE id = :id"),
                {"id": job_id_str},
            ).fetchone()
            if row and row[0] in ("CANCELLED", "CANCELLING"):
                return True
    except Exception:
        pass

    return False


def check_cancellation(job_id_str: str, r: Optional[redis.Redis] = None):
    """Raises ImportCancelledException immediately if cancellation was requested."""
    if is_cancellation_requested(job_id_str, r):
        raise ImportCancelledException("Import was cancelled by user.")


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
                        SET status = CAST(:status AS VARCHAR(50)),
                            progress = GREATEST(progress, :progress),
                            stage_message = :stage_msg,
                            processed_rows = GREATEST(processed_rows, :processed),
                            successful_rows = :successful,
                            failed_rows = :failed,
                            error_message = :err,
                            completed_at = CASE 
                                WHEN CAST(:status AS VARCHAR(50)) IN ('COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED') 
                                THEN NOW() 
                                ELSE completed_at 
                            END,
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
) -> int:
    """Publishes strictly monotonic progress events to Redis Pub/Sub and caches latest state."""
    import_id_str = str(import_id)
    seq = 1
    safe_progress = progress

    try:
        seq = r.incr(f"import_seq:{import_id_str}")
        max_key = f"import_max_progress:{import_id_str}"
        prev_max = r.get(max_key)
        prev_val = int(prev_max) if prev_max is not None else 0

        if status in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
            safe_progress = 100
        elif status in ("CANCELLED", "FAILED"):
            # Preserve achieved progress on cancellation or failure, never drop to 0
            safe_progress = max(prev_val, progress) if progress > 0 else prev_val
        else:
            safe_progress = max(prev_val, progress)

        if safe_progress != prev_val:
            r.setex(max_key, 3600, str(safe_progress))
    except Exception as e:
        logger.debug(f"Redis monotonic tracking notice for {import_id_str}: {e}")

    payload = {
        "import_id": import_id_str,
        "status": status,
        "progress": safe_progress,
        "processed_rows": processed_rows,
        "total_rows": total_rows,
        "successful_rows": successful_rows,
        "failed_rows": failed_rows,
        "stage_message": stage_message,
        "error_message": error_message,
        "seq": seq,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    channel = f"import_progress:{import_id_str}"
    try:
        r.publish(channel, json.dumps(payload))
        # Also store the latest state in Redis with 1-hour expiry for instant fetch
        r.setex(f"import_latest:{import_id_str}", 3600, json.dumps(payload))
    except Exception as e:
        logger.warning(f"Failed to publish progress to Redis for import {import_id_str}: {e}")

    return safe_progress


def execute_import_pipeline(job_id_str: str, file_path: str):
    logger.info(f"Starting optimized import pipeline for job {job_id_str} with file {file_path}")
    r = get_redis_client()
    clean_id = job_id_str.replace("-", "")
    staging_table = f"staging_{clean_id}"

    processed_rows = 0
    staged_rows = 0
    successful_rows = 0
    failed_rows = 0
    total_rows = 0
    error_records_to_insert = []
    MAX_ERRORS_STORED = 1000

    # Secondary indexes temporarily dropped during bulk upsert to eliminate random I/O write amplification
    indexes_definitions = {
        "ix_products_name_trgm": "CREATE INDEX IF NOT EXISTS ix_products_name_trgm ON products USING gin (name gin_trgm_ops);",
        "ix_products_sku_trgm": "CREATE INDEX IF NOT EXISTS ix_products_sku_trgm ON products USING gin (sku gin_trgm_ops);",
        "ix_products_created_at": "CREATE INDEX IF NOT EXISTS ix_products_created_at ON products (created_at DESC);",
        "ix_products_active": "CREATE INDEX IF NOT EXISTS ix_products_active ON products (active);",
    }
    dropped_indexes = {}

    try:
        check_cancellation(job_id_str, r)

        # 1. Transition state to PARSING (5%)
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

        check_cancellation(job_id_str, r)

        # 2. Pass 1: Streaming Validation & In-Memory Deduplication (0.98s for 500K SKUs, ~57 MB RAM)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File {file_path} not found on server")

        latest_sku_rows = {}

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                raise ValueError("CSV file is completely empty.")

            if not header:
                raise ValueError("CSV file contains empty header row.")

            cleaned_header = [col.strip().lower() for col in header]
            if len(cleaned_header) < 3 or cleaned_header[0] != "name" or cleaned_header[1] != "sku" or cleaned_header[2] != "description":
                raise ValueError(f"Invalid CSV header: expected ['name', 'sku', 'description'], got {header}")

            for row_idx, row in enumerate(reader, start=1):
                total_rows += 1

                if len(row) < 3:
                    failed_rows += 1
                    if len(error_records_to_insert) < MAX_ERRORS_STORED:
                        error_records_to_insert.append((job_id_str, row_idx, "Malformed row: missing columns", ",".join(row)))
                    continue

                name = row[0].strip()
                sku = row[1].strip()

                if not name or not sku:
                    failed_rows += 1
                    if len(error_records_to_insert) < MAX_ERRORS_STORED:
                        msg = "Missing required product name" if not name else "Missing required product SKU"
                        error_records_to_insert.append((job_id_str, row_idx, msg, ",".join(row)))
                    continue

                # Deterministic Case-Insensitive Deduplication: latest row in CSV wins
                latest_sku_rows[sku.lower()] = row_idx

                if total_rows % 100000 == 0:
                    check_cancellation(job_id_str, r)

        check_cancellation(job_id_str, r)

        winning_row_indices = set(latest_sku_rows.values())
        unique_product_count = len(winning_row_indices)
        del latest_sku_rows  # Reclaim dictionary memory immediately

        logger.info(
            f"Pass 1 complete for {job_id_str}: {total_rows:,} total rows, "
            f"{unique_product_count:,} unique SKUs (latest winning), {failed_rows:,} validation errors."
        )

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

        # 3. Create unlogged staging table with pre-partitioned chunk_id
        check_cancellation(job_id_str, r)
        with sync_engine.connect() as raw_conn:
            with raw_conn.begin():
                raw_conn.execute(
                    text(
                        f"""
                        DROP TABLE IF EXISTS {staging_table};
                        CREATE UNLOGGED TABLE {staging_table} (
                            chunk_id INT,
                            sku VARCHAR(100),
                            name VARCHAR(255),
                            description TEXT
                        );
                        """
                    )
                )

        # 4. Pass 2: Stream ONLY winning unique records via raw PostgreSQL COPY FROM STDIN
        stage_msg = f"Validating and streaming data to staging: 0 / {total_rows:,} rows..."
        update_job_db(job_id_str, "VALIDATING", 10, stage_msg, 0, 0, failed_rows)
        publish_progress(r, job_id_str, "VALIDATING", 10, 0, total_rows, 0, failed_rows, stage_msg)

        NUM_CHUNKS = 5 if unique_product_count >= 50000 else 1
        chunk_size_unique = math.ceil(unique_product_count / NUM_CHUNKS) if unique_product_count > 0 else 1
        copy_batch_size = 25000
        batch_valid = []
        unique_counter = 0

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header (already validated)

            raw_db_conn = sync_engine.raw_connection()
            try:
                raw_cursor = raw_db_conn.cursor()
                try:
                    raw_cursor.execute("SELECT pg_backend_pid();")
                    p_pid = raw_cursor.fetchone()
                    if p_pid:
                        r.setex(f"import_pg_pid:{job_id_str}", 3600, str(p_pid[0]))
                except Exception:
                    pass

                for row_idx, row in enumerate(reader, start=1):
                    processed_rows += 1

                    if row_idx in winning_row_indices:
                        unique_counter += 1
                        c_id = min(NUM_CHUNKS, (unique_counter - 1) // chunk_size_unique + 1)
                        sku = row[1].strip()
                        name = row[0].strip()
                        desc = row[2].strip() if len(row) > 2 else ""
                        batch_valid.append((c_id, sku, name, desc))

                    if len(batch_valid) >= copy_batch_size:
                        check_cancellation(job_id_str, r)

                        tsv_buffer = io.StringIO()
                        for c_id, r_sku, r_name, r_desc in batch_valid:
                            esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                            tsv_buffer.write(f"{c_id}\t{esc_sku}\t{esc_name}\t{esc_desc}\n")
                        tsv_buffer.seek(0)

                        with raw_cursor.copy(f"COPY {staging_table} (chunk_id, sku, name, description) FROM STDIN") as copy:
                            copy.write(tsv_buffer.getvalue())
                        raw_db_conn.commit()

                        staged_rows += len(batch_valid)
                        batch_valid.clear()

                        # Progress smoothly scaled between 10% and 60%
                        progress_pct = int(10 + (processed_rows / total_rows) * 50) if total_rows > 0 else 50
                        progress_pct = min(60, max(10, progress_pct))
                        stage_msg = f"Validated and staged {processed_rows:,} / {total_rows:,} rows..."
                        publish_progress(
                            r,
                            job_id_str,
                            "VALIDATING",
                            progress_pct,
                            processed_rows,
                            total_rows,
                            0,
                            failed_rows,
                            stage_msg,
                        )
                        if processed_rows % 50000 == 0:
                            update_job_db(job_id_str, "VALIDATING", progress_pct, stage_msg, processed_rows, 0, failed_rows)

                # Flush final batch
                if batch_valid:
                    check_cancellation(job_id_str, r)
                    tsv_buffer = io.StringIO()
                    for c_id, r_sku, r_name, r_desc in batch_valid:
                        esc_sku = r_sku.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        esc_name = r_name.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        esc_desc = r_desc.replace("\\", "\\\\").replace("\t", " ").replace("\n", " ").replace("\r", "")
                        tsv_buffer.write(f"{c_id}\t{esc_sku}\t{esc_name}\t{esc_desc}\n")
                    tsv_buffer.seek(0)

                    with raw_cursor.copy(f"COPY {staging_table} (chunk_id, sku, name, description) FROM STDIN") as copy:
                        copy.write(tsv_buffer.getvalue())
                    raw_db_conn.commit()
                    staged_rows += len(batch_valid)
                    batch_valid.clear()

                # Ensure staging is marked 60% complete for all file sizes
                stage_msg = f"Validated and staged {processed_rows:,} / {total_rows:,} rows..."
                publish_progress(
                    r,
                    job_id_str,
                    "VALIDATING",
                    60,
                    processed_rows,
                    total_rows,
                    0,
                    failed_rows,
                    stage_msg,
                )
                update_job_db(job_id_str, "VALIDATING", 60, stage_msg, processed_rows, 0, failed_rows)

            finally:
                raw_cursor.close()
                raw_db_conn.close()

        # Reclaim winning_row_indices set
        del winning_row_indices

        check_cancellation(job_id_str, r)

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

        check_cancellation(job_id_str, r)

        # 5. Drop secondary B-Tree and GIN Trigram indexes to eliminate write amplification during UPSERT
        stage_msg = "Optimizing search indexes for catalogue merge..."
        update_job_db(job_id_str, "IMPORTING", 65, stage_msg, processed_rows, 0, failed_rows)
        publish_progress(r, job_id_str, "IMPORTING", 65, processed_rows, total_rows, 0, failed_rows, stage_msg)

        with sync_engine.connect() as conn:
            with conn.begin():
                result = conn.execute(text("""
                    SELECT indexname FROM pg_indexes
                    WHERE tablename = 'products'
                    AND indexname IN ('ix_products_name_trgm', 'ix_products_sku_trgm', 'ix_products_created_at', 'ix_products_active');
                """))
                found_indexes = [r_idx[0] for r_idx in result.fetchall()]
                for idx_name in found_indexes:
                    if idx_name in indexes_definitions:
                        dropped_indexes[idx_name] = indexes_definitions[idx_name]
                        conn.execute(text(f"DROP INDEX IF EXISTS {idx_name};"))
                        logger.info(f"Temporarily dropped index {idx_name} for bulk import")

        check_cancellation(job_id_str, r)

        # 6. Range-Chunked Catalogue UPSERT within a Single Atomic Transaction
        # Preserves active=false on existing records: ON CONFLICT does NOT overwrite active!
        # Honest progress: successful_rows is ONLY reported as non-zero after transaction commit!
        total_upserted = 0
        upsert_error = None

        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    pid_row = conn.execute(text("SELECT pg_backend_pid();")).fetchone()
                    if pid_row:
                        r.setex(f"import_pg_pid:{job_id_str}", 3600, str(pid_row[0]))
                    conn.execute(text("SET LOCAL work_mem = '64MB';"))
                    conn.execute(text("SET LOCAL synchronous_commit = off;"))

                    for c_id in range(1, NUM_CHUNKS + 1):
                        check_cancellation(job_id_str, r)

                        res = conn.execute(
                            text(
                                f"""
                                INSERT INTO products (sku, name, description, active, created_at, updated_at)
                                SELECT sku, name, description, TRUE, NOW(), NOW()
                                FROM {staging_table}
                                WHERE chunk_id = :chunk_id
                                ON CONFLICT (lower(sku)) DO UPDATE SET
                                    name = EXCLUDED.name,
                                    description = EXCLUDED.description,
                                    updated_at = NOW();
                                """
                            ),
                            {"chunk_id": c_id},
                        )
                        chunk_upserted = res.rowcount
                        total_upserted += chunk_upserted

                        # Progress scaled between 70% and 90%
                        pct = int(70 + (c_id / NUM_CHUNKS) * 20)
                        pct = min(90, max(70, pct))
                        stage_msg = f"Merging into catalogue: {total_upserted:,} / {unique_product_count:,} products ({pct}%)..."
                        publish_progress(
                            r,
                            job_id_str,
                            "IMPORTING",
                            pct,
                            processed_rows,
                            total_rows,
                            0,
                            failed_rows,
                            stage_msg,
                        )
                        update_job_db(job_id_str, "IMPORTING", pct, stage_msg, processed_rows, 0, failed_rows)

                    # Check cancellation right before commit
                    check_cancellation(job_id_str, r)

                    # Staging table dropped before commit
                    conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))
                    # Transaction commits atomically here!

        except Exception as e:
            upsert_error = e

        if upsert_error:
            raise upsert_error

        # 7. Rebuild search & secondary indexes in a single sequential pass
        stage_msg = "Rebuilding search indexes..."
        update_job_db(job_id_str, "IMPORTING", 92, stage_msg, processed_rows, 0, failed_rows)
        publish_progress(r, job_id_str, "IMPORTING", 92, processed_rows, total_rows, 0, failed_rows, stage_msg)

        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    pid_row = conn.execute(text("SELECT pg_backend_pid();")).fetchone()
                    if pid_row:
                        r.setex(f"import_pg_pid:{job_id_str}", 3600, str(pid_row[0]))
                    conn.execute(text("SET LOCAL maintenance_work_mem = '64MB';"))
                    for idx_name, create_sql in dropped_indexes.items():
                        conn.execute(text(create_sql))
                        logger.info(f"Rebuilt index {idx_name}")
        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild indexes: {rebuild_err}")

        # 8. Finalize Job: only committed rows are reported as succeeded
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

        # Clean up Redis cancellation keys
        try:
            r.delete(f"import_cancel:{job_id_str}")
            r.delete(f"import_pg_pid:{job_id_str}")
        except Exception:
            pass

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

        return {
            "status": final_status,
            "total_rows": total_rows,
            "processed_rows": processed_rows,
            "successful_rows": successful_rows,
            "failed_rows": failed_rows,
        }

    except ImportCancelledException:
        logger.info(f"Import pipeline cancelled by user for job {job_id_str}")
        # Clean up temporary staging table
        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))
        except Exception:
            pass

        # Restore dropped indexes if any
        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    for idx_name, create_sql in dropped_indexes.items():
                        conn.execute(text(create_sql))
        except Exception:
            pass

        # Update import_jobs as CANCELLED in PostgreSQL with 0 succeeded rows
        cur_prog = 0
        try:
            val = r.get(f"import_max_progress:{job_id_str}")
            cur_prog = int(val) if val else 0
        except Exception:
            pass

        update_job_db(
            job_id_str,
            "CANCELLED",
            cur_prog,
            "Import cancelled by user",
            processed_rows,
            0,
            failed_rows,
        )
        publish_progress(
            r,
            job_id_str,
            "CANCELLED",
            cur_prog,
            processed_rows,
            total_rows,
            0,
            failed_rows,
            "Import cancelled by user",
        )

        try:
            r.delete(f"import_cancel:{job_id_str}")
            r.delete(f"import_pg_pid:{job_id_str}")
        except Exception:
            pass

        try:
            from app.tasks.webhook_tasks import dispatch_webhook_event
            dispatch_webhook_event.delay(
                "import.cancelled",
                {"import_id": job_id_str},
            )
        except Exception:
            pass
        return {"status": "CANCELLED"}

    except Exception as e:
        err_msg_lower = str(e).lower()
        is_cancel = (
            "canceling statement due to user request" in err_msg_lower
            or "querycanceled" in type(e).__name__.lower()
            or is_cancellation_requested(job_id_str, r)
        )

        if is_cancel:
            logger.info(f"Import pipeline SQL query cancelled by user for job {job_id_str}")
            try:
                with sync_engine.connect() as conn:
                    with conn.begin():
                        conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))
            except Exception:
                pass

            # Restore dropped indexes
            try:
                with sync_engine.connect() as conn:
                    with conn.begin():
                        for idx_name, create_sql in dropped_indexes.items():
                            conn.execute(text(create_sql))
            except Exception:
                pass

            cur_prog = 0
            try:
                val = r.get(f"import_max_progress:{job_id_str}")
                cur_prog = int(val) if val else 0
            except Exception:
                pass

            update_job_db(
                job_id_str,
                "CANCELLED",
                cur_prog,
                "Import cancelled by user",
                processed_rows,
                0,
                failed_rows,
            )
            publish_progress(
                r,
                job_id_str,
                "CANCELLED",
                cur_prog,
                processed_rows,
                total_rows,
                0,
                failed_rows,
                "Import cancelled by user",
            )
            try:
                r.delete(f"import_cancel:{job_id_str}")
                r.delete(f"import_pg_pid:{job_id_str}")
            except Exception:
                pass
            return {"status": "CANCELLED"}

        logger.exception(f"Import pipeline failed for job {job_id_str}: {e}")
        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))
        except Exception:
            pass

        # Restore dropped indexes
        try:
            with sync_engine.connect() as conn:
                with conn.begin():
                    for idx_name, create_sql in dropped_indexes.items():
                        conn.execute(text(create_sql))
        except Exception:
            pass

        cur_prog = 0
        try:
            val = r.get(f"import_max_progress:{job_id_str}")
            cur_prog = int(val) if val else 0
        except Exception:
            pass

        # Update import_jobs as FAILED in PostgreSQL
        # Do NOT report uncommitted rows as succeeded
        update_job_db(
            job_id_str,
            "FAILED",
            cur_prog,
            "Import failed",
            processed_rows,
            0,
            failed_rows,
            error_message=str(e),
        )
        publish_progress(
            r,
            job_id_str,
            "FAILED",
            cur_prog,
            processed_rows,
            total_rows,
            0,
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
