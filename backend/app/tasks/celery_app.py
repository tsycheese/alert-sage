from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.observability.logging import configure_logging
from app.observability.worker import configure_worker_metrics

settings = get_settings()

celery_app = Celery(
    "alert_sage",
    broker=settings.celery_broker_url,
    include=["app.tasks.workflows", "app.tasks.cases"],
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
configure_worker_metrics()


@setup_logging.connect(weak=False)
def configure_worker_logging(**_: object) -> None:
    worker_settings = get_settings()
    configure_logging(
        service="alert-sage-worker",
        environment=worker_settings.environment,
        level=worker_settings.log_level,
    )
