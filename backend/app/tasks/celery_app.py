from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.observability.logging import configure_logging
from app.observability.worker import configure_worker_metrics

settings = get_settings()
if settings.component_role not in {"worker", "relay", "test"}:
    raise RuntimeError("Celery must run with ALERT_SAGE_COMPONENT_ROLE=worker or relay")

celery_app = Celery(
    "alert_sage",
    broker=settings.celery_broker_url,
    include=[
        "app.tasks.workflows",
        "app.tasks.cases",
        "app.tasks.outbox",
        "app.tasks.evaluations",
        "app.tasks.feishu",
    ],
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
    beat_schedule={
        "publish-transactional-outbox": {
            "task": "alert_sage.outbox.publish",
            "schedule": settings.outbox_poll_interval_seconds,
            "options": {"expires": settings.outbox_poll_interval_seconds},
        }
    },
)
configure_worker_metrics()


@setup_logging.connect(weak=False)
def configure_worker_logging(**_: object) -> None:
    worker_settings = get_settings()
    configure_logging(
        service=worker_settings.celery_log_service,
        environment=worker_settings.environment,
        level=worker_settings.log_level,
    )
