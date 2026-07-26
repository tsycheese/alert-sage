import asyncio
import logging
from time import monotonic
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.observability.logging import bind_log_context, correlation_from_celery_headers
from app.services.outbox import OutboxRelayResult, OutboxRelayService
from app.tasks.celery_app import celery_app
from app.tasks.dispatcher import CeleryOutboxPublisher

logger = logging.getLogger(__name__)


async def _publish_outbox() -> OutboxRelayResult:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        return await OutboxRelayService(
            session_factory=session_factory,
            publisher=CeleryOutboxPublisher(),
            batch_size=settings.outbox_batch_size,
            retry_base_seconds=settings.outbox_retry_base_seconds,
            retry_max_seconds=settings.outbox_retry_max_seconds,
        ).publish_batch()
    finally:
        await engine.dispose()


@celery_app.task(name="alert_sage.outbox.publish", bind=True)
def run_outbox_publish(task: Any) -> None:
    started = monotonic()
    with bind_log_context(
        **{
            **correlation_from_celery_headers(getattr(task.request, "headers", None)),
            "task_id": getattr(task.request, "id", None),
        }
    ):
        status = "failed"
        error_type: str | None = None
        logger.info("outbox.relay.started", extra={"operation": "outbox.publish_batch"})
        try:
            result = asyncio.run(_publish_outbox())
            status = "completed"
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            logger.log(
                logging.ERROR if error_type else logging.INFO,
                "outbox.relay.completed",
                extra={
                    "operation": "outbox.publish_batch",
                    "status": status,
                    "result_count": result.selected if status == "completed" else None,
                    "duration_ms": round(max(0.0, monotonic() - started) * 1000, 3),
                    "error_type": error_type,
                },
            )
