import logging
from app.core.celery_app import celery_app
from app.services.import_service import execute_import_pipeline

logger = logging.getLogger("import_tasks")


@celery_app.task(bind=True, name="app.tasks.import_tasks.process_csv_import")
def process_csv_import(self, job_id: str, file_path: str):
    logger.info(f"Celery task started: process_csv_import for job {job_id}")
    return execute_import_pipeline(job_id, file_path)
