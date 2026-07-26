from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import OutboxStatus, OutboxTopic
from app.models.outbox import OutboxMessage
from app.observability.logging import (
    CORRELATION_FIELDS,
    bind_log_context,
    get_log_context,
    normalize_context,
)
from app.observability.metrics import observe_outbox_delivery

logger = logging.getLogger(__name__)


class OutboxEnqueueConflictError(Exception):
    pass


class OutboxPublisher(Protocol):
    def publish(self, message: OutboxMessage) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxRelayResult:
    selected: int
    published: int
    deferred: int


async def enqueue_outbox_message(
    session: AsyncSession,
    *,
    topic: OutboxTopic,
    aggregate_id: UUID,
    idempotency_key: str,
    payload: dict[str, object],
    correlation: Mapping[str, object] | None = None,
) -> tuple[OutboxMessage, bool]:
    existing = await session.scalar(
        select(OutboxMessage).where(OutboxMessage.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if (
            existing.topic != topic
            or existing.aggregate_id != aggregate_id
            or existing.payload != payload
        ):
            raise OutboxEnqueueConflictError(
                f"outbox idempotency key {idempotency_key!r} has conflicting content"
            )
        return existing, False

    stored_correlation = {
        key: value for key, value in get_log_context().items() if key in CORRELATION_FIELDS
    }
    stored_correlation.update(normalize_context(correlation or {}))
    message = OutboxMessage(
        topic=topic,
        aggregate_id=aggregate_id,
        idempotency_key=idempotency_key,
        payload=payload,
        correlation=stored_correlation,
    )
    session.add(message)
    await session.flush()
    return message, True


async def next_delivery_number(
    session: AsyncSession,
    *,
    topic: OutboxTopic,
    aggregate_id: UUID,
) -> int:
    current = await session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(
            OutboxMessage.topic == topic,
            OutboxMessage.aggregate_id == aggregate_id,
        )
    )
    return int(current or 0) + 1


class OutboxRelayService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: OutboxPublisher,
        batch_size: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.publisher = publisher
        self.batch_size = batch_size
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds

    async def publish_batch(self) -> OutboxRelayResult:
        now = datetime.now(UTC)
        published = 0
        deferred = 0
        async with self.session_factory() as session, session.begin():
            messages = list(
                await session.scalars(
                    select(OutboxMessage)
                    .where(
                        OutboxMessage.status == OutboxStatus.PENDING,
                        OutboxMessage.available_at <= now,
                    )
                    .order_by(OutboxMessage.created_at, OutboxMessage.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            for message in messages:
                if self._publish_one(message, now):
                    published += 1
                else:
                    deferred += 1
        if messages:
            logger.info(
                "outbox.batch.completed",
                extra={
                    "operation": "outbox.publish_batch",
                    "status": "completed",
                    "result_count": len(messages),
                },
            )
        return OutboxRelayResult(
            selected=len(messages),
            published=published,
            deferred=deferred,
        )

    def _publish_one(self, message: OutboxMessage, now: datetime) -> bool:
        started = monotonic()
        message.attempts += 1
        retry_delay: float | None = None
        status = "published"
        error_type: str | None = None
        delivery_context: dict[str, object] = {
            **message.correlation,
            "outbox_message_id": message.id,
        }
        with bind_log_context(**delivery_context):
            try:
                self.publisher.publish(message)
            except Exception as exc:  # noqa: BLE001 - the durable row records retry state
                status = "deferred"
                error_type = type(exc).__name__
                retry_delay = self._retry_delay(message.attempts)
                message.available_at = now + timedelta(seconds=retry_delay)
                message.last_error_type = error_type[:128]
            else:
                message.status = OutboxStatus.PUBLISHED
                message.published_at = now
                message.last_error_type = None
            finally:
                duration_seconds = max(0.0, monotonic() - started)
                logger.log(
                    logging.WARNING if error_type else logging.INFO,
                    "outbox.delivery.completed",
                    extra={
                        "operation": "outbox.publish",
                        "topic": str(message.topic),
                        "status": status,
                        "attempt": message.attempts,
                        "duration_ms": round(duration_seconds * 1000, 3),
                        "error_type": error_type,
                    },
                )
                observe_outbox_delivery(
                    topic=str(message.topic),
                    status=status,
                    duration_seconds=duration_seconds,
                    retry_delay_seconds=retry_delay,
                )
        return error_type is None

    def _retry_delay(self, attempts: int) -> float:
        exponent = min(max(0, attempts - 1), 20)
        return min(self.retry_max_seconds, self.retry_base_seconds * (2**exponent))
