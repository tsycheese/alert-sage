import asyncio
from contextlib import suppress
from time import monotonic
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.feishu.client import FeishuApiError, FeishuClient
from app.integrations.feishu.delivery import FeishuDeliveryService
from app.models.enums import FeishuDeliveryStatus
from app.models.feishu import FeishuDelivery
from app.observability.logging import bind_log_context, correlation_from_celery_headers
from app.observability.metrics import (
    observe_feishu_delivery,
    observe_feishu_pending_deliveries,
)
from app.tasks.celery_app import celery_app


async def _deliver(delivery_id: UUID) -> None:
    settings = get_settings()
    if not settings.feishu_enabled:
        raise FeishuApiError("feishu_disabled")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    client = FeishuClient(settings=settings, redis=redis)
    started = monotonic()
    kind = "unknown"
    result = "failed"
    try:
        async with session_factory() as session:
            stored_kind = await session.scalar(
                select(FeishuDelivery.kind).where(FeishuDelivery.id == delivery_id)
            )
            kind = str(getattr(stored_kind, "value", stored_kind or "unknown"))
        delivery_result = await FeishuDeliveryService(
            session_factory=session_factory,
            client=client,
            settings=settings,
        ).execute(delivery_id)
        result = "stale" if delivery_result.skipped_stale else "succeeded"
    finally:
        observe_feishu_delivery(
            kind=kind,
            operation="deliver",
            result=result,
            duration_seconds=max(0.0, monotonic() - started),
        )
        with suppress(Exception):
            async with session_factory() as session:
                pending = await session.scalar(
                    select(func.count())
                    .select_from(FeishuDelivery)
                    .where(
                        FeishuDelivery.status.in_(
                            {
                                FeishuDeliveryStatus.PENDING,
                                FeishuDeliveryStatus.PROCESSING,
                            }
                        )
                    )
                )
                observe_feishu_pending_deliveries(int(pending or 0))
        await client.close()
        with suppress(Exception):
            await redis.aclose()
        await engine.dispose()


@celery_app.task(name="alert_sage.feishu.deliver", bind=True, max_retries=5)
def deliver_feishu_message(task: Any, delivery_id: str) -> None:
    stored_id = UUID(delivery_id)
    with bind_log_context(
        **{
            **correlation_from_celery_headers(getattr(task.request, "headers", None)),
            "feishu_delivery_id": stored_id,
            "task_id": getattr(task.request, "id", None),
        }
    ):
        try:
            asyncio.run(_deliver(stored_id))
        except FeishuApiError as exc:
            retries = int(getattr(task.request, "retries", 0))
            raise task.retry(exc=exc, countdown=min(2**retries, 60)) from exc
