from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import OutboxStatus, OutboxTopic


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="outbox_messages_idempotency_key"),
        CheckConstraint(
            "topic IN ('workflow.start', 'workflow.resume', 'workflow.retry', 'case.sync')",
            name="topic_values",
        ),
        CheckConstraint(
            "status IN ('pending', 'published')",
            name="status_values",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index(
            "ix_outbox_messages_pending_available",
            "available_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_outbox_messages_aggregate", "topic", "aggregate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic: Mapped[OutboxTopic] = mapped_column(String(48))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    status: Mapped[OutboxStatus] = mapped_column(
        String(16),
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
