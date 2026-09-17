import asyncio
import json
import logging
import os
import uuid
from typing import List
from fastapi import APIRouter, Depends, File, UploadFile, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from app.core.database import get_async_db
from app.core.config import settings
from app.models.import_job import ImportJob, ImportErrorRecord
from app.schemas.import_job import ImportJobResponse, ImportJobDetailResponse
from app.core.exceptions import ImportNotFoundException, InvalidCsvException
from app.tasks.import_tasks import process_csv_import

router = APIRouter(prefix="/imports", tags=["Imports"])
logger = logging.getLogger("imports_api")


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_csv_and_start_import(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Accepts CSV file upload, streams it directly to disk, creates an import job,
    and enqueues background Celery worker processing without blocking the API.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise InvalidCsvException("Only CSV files (.csv) are supported.")

    job_id = uuid.uuid4()
    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    temp_path = os.path.join(upload_dir, f"{job_id}.csv")

    # Stream file to disk in bounded 1MB chunks - never load entire file in memory
    total_bytes = 0
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    try:
        with open(temp_path, "wb") as f_out:
            while chunk := await file.read(4 * 1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise InvalidCsvException(
                        f"File size exceeds maximum allowed limit of {settings.MAX_UPLOAD_SIZE_MB}MB."
                    )
                await asyncio.to_thread(f_out.write, chunk)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

    # Create import job record in PostgreSQL
    job = ImportJob(
        id=job_id,
        filename=file.filename,
        status="QUEUED",
        stage_message="Import job queued for background processing",
        progress=0,
    )
    db.add(job)
    await db.flush()

    # Enqueue Celery background task
    process_csv_import.delay(str(job_id), temp_path)

    return {
        "import_id": str(job_id),
        "status": "QUEUED",
        "filename": file.filename,
        "message": "CSV upload accepted. Processing started in background.",
    }


@router.get("", response_model=List[ImportJobResponse])
async def list_import_jobs(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """List recent import jobs ordered by creation date."""
    stmt = select(ImportJob).order_by(ImportJob.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


from sqlalchemy.orm import selectinload

@router.get("/{import_id}", response_model=ImportJobDetailResponse)
async def get_import_job(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get full details and validation error list for a specific import job."""
    stmt = (
        select(ImportJob)
        .options(selectinload(ImportJob.errors))
        .where(ImportJob.id == import_id)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        raise ImportNotFoundException(str(import_id))

    return job


@router.get("/{import_id}/progress")
async def stream_import_progress(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Real-time Server-Sent Events (SSE) endpoint for tracking CSV import progress.
    Streams live state transitions and auto-disconnects on completion.
    """
    import_id_str = str(import_id)

    # 1. Fetch initial status: check Redis cache first, then fallback to database
    initial_payload = None
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        cached_raw = await r.get(f"import_latest:{import_id_str}")
        if cached_raw:
            initial_payload = json.loads(cached_raw)
    except Exception:
        pass

    if not initial_payload:
        stmt = select(ImportJob).where(ImportJob.id == import_id)
        result = await db.execute(stmt)
        job = result.scalar_one_or_none()
        if not job:
            await r.aclose()
            raise ImportNotFoundException(import_id_str)

        initial_payload = {
            "import_id": import_id_str,
            "status": job.status,
            "progress": job.progress,
            "processed_rows": job.processed_rows,
            "total_rows": job.total_rows,
            "successful_rows": job.successful_rows,
            "failed_rows": job.failed_rows,
            "stage_message": job.stage_message,
            "error_message": job.error_message,
        }

    async def sse_event_generator():
        # Yield current initial state immediately
        yield f"event: progress\ndata: {json.dumps(initial_payload)}\n\n"

        # If already completed or failed, terminate stream immediately
        if initial_payload["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
            await r.aclose()
            return

        # Connect to Redis Pub/Sub
        pubsub = r.pubsub()
        channel = f"import_progress:{import_id_str}"
        await pubsub.subscribe(channel)

        try:
            while True:
                # Wait for pubsub message or send heartbeat ping every 2.5s to keep proxy alive
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5),
                        timeout=2.5,
                    )
                except asyncio.TimeoutError:
                    # Authoritative database check: if job reached terminal status in DB, exit immediately!
                    try:
                        stmt = select(ImportJob).where(ImportJob.id == import_id)
                        res = await db.execute(stmt)
                        current_db_job = res.scalar_one_or_none()
                        if current_db_job and current_db_job.status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                            term_payload = {
                                "import_id": import_id_str,
                                "status": current_db_job.status,
                                "progress": 100 if current_db_job.status == "COMPLETED" else current_db_job.progress,
                                "processed_rows": current_db_job.processed_rows,
                                "total_rows": current_db_job.total_rows,
                                "successful_rows": current_db_job.successful_rows,
                                "failed_rows": current_db_job.failed_rows,
                                "stage_message": current_db_job.stage_message,
                                "error_message": current_db_job.error_message,
                            }
                            yield f"event: progress\ndata: {json.dumps(term_payload)}\n\n"
                            break
                    except Exception:
                        pass

                    # Heartbeat comment to keep connection alive through reverse proxies
                    yield ": ping\n\n"
                    continue

                if message and message["type"] == "message":
                    raw_data = message["data"]
                    yield f"event: progress\ndata: {raw_data}\n\n"

                    # Check for terminal state to cleanly close SSE connection
                    try:
                        parsed = json.loads(raw_data)
                        if parsed.get("status") in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                            break
                    except Exception:
                        pass

                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            logger.info(f"SSE client disconnected for import {import_id_str}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await r.aclose()

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
