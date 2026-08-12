from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    FeishuCallbackStatus,
    FeishuCardStatus,
    FeishuDeliveryKind,
    FeishuDeliveryStatus,
)


class FeishuCardBinding(Base):
    __tablename__ = "feishu_card_bindings"
    __table_args__ = (
        UniqueConstraint("alert_id", name="feishu_card_bindings_alert_id"),
        CheckConstraint("desired_revision >= 1", name="desired_revision_positive"),
        CheckConstraint(
            "delivered_revision >= 0 AND delivered_revision <= desired_revision",
            name="delivered_revision_range",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'failed', 'replaced')",
            name="status_values",
        ),
        Index("ix_feishu_card_bindings_status_updated", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE")
    )
    app_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str] = mapped_column(String(128))
    message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    desired_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    delivered_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    action_nonce_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[FeishuCardStatus] = mapped_column(
        String(16), default=FeishuCardStatus.PENDING, server_default="pending"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FeishuCallbackEvent(Base):
    __tablename__ = "feishu_callback_events"
    __table_args__ = (
        UniqueConstraint("app_id", "event_id", name="feishu_callback_events_app_event"),
        CheckConstraint(
            "status IN ('received', 'processed', 'rejected', 'failed')",
            name="status_values",
        ),
        Index("ix_feishu_callback_events_received", "received_at"),
        Index("ix_feishu_callback_events_binding", "binding_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(128))
    binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feishu_card_bindings.id", ondelete="SET NULL"),
        nullable=True,
    )
    open_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_timestamp: Mapped[int] = mapped_column(BigInteger)
    raw_body_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[FeishuCallbackStatus] = mapped_column(
        String(16), default=FeishuCallbackStatus.RECEIVED, server_default="received"
    )
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeishuDelivery(Base):
    __tablename__ = "feishu_deliveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="feishu_deliveries_idempotency_key"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "kind IN ('card_sync', 'private_reminder')",
            name="kind_values",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed')",
            name="status_values",
        ),
        Index("ix_feishu_deliveries_status_updated", "status", "updated_at"),
        Index("ix_feishu_deliveries_binding_revision", "binding_id", "revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("feishu_card_bindings.id", ondelete="CASCADE")
    )
    kind: Mapped[FeishuDeliveryKind] = mapped_column(String(24))
    recipient_open_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id"),
        nullable=True,
    )
    revision: Mapped[int] = mapped_column(Integer)
    action_nonce: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[FeishuDeliveryStatus] = mapped_column(
        String(16), default=FeishuDeliveryStatus.PENDING, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
