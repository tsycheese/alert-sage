from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "alert_sage",
    broker=settings.celery_broker_url,
    include=["app.tasks.workflows"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
)
