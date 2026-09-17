from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "opm_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.import_tasks",
        "app.tasks.webhook_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour maximum for massive imports
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.tasks.import_tasks.*": {"queue": "imports"},
        "app.tasks.webhook_tasks.*": {"queue": "webhooks"},
    },
)
