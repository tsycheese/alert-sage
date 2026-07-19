import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AlertSeverity, AlertStatus

if TYPE_CHECKING:
    from app.models.workflow import WorkflowRun


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("source", "external_alert_id", name="source_external_alert_id"),
        CheckConstraint("severity IN ('info', 'warning', 'critical')", name="severity_values"),
        CheckConstraint(
            "status IN ('received', 'running', 'waiting_for_approval', 'reanalyzing', "
            "'completed', 'rejected', 'failed')",
            name="status_values",
        ),
        Index("ix_alerts_created_at_desc", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), default="web")
    external_alert_id: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    alert_name: Mapped[str] = mapped_column(String(128))
    service: Mapped[str] = mapped_column(String(128), index=True)
    instance: Mapped[str | None] = mapped_column(String(128), nullable=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        String(16), default=AlertSeverity.WARNING, index=True
    )
    status: Mapped[AlertStatus] = mapped_column(
        String(32), default=AlertStatus.RECEIVED, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="alert")
