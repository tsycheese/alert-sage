from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import WorkflowEventType, WorkflowRunStatus

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.diagnosis import DiagnosisReport
    from app.models.tool_execution import ToolExecution


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("thread_id", name="workflow_runs_thread_id"),
        UniqueConstraint("idempotency_key", name="workflow_runs_idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_for_approval', 'reanalyzing', "
            "'completed', 'rejected', 'failed')",
            name="status_values",
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="valid_time_range",
        ),
        Index(
            "uq_workflow_runs_alert_active",
            "alert_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'running', 'waiting_for_approval', 'reanalyzing')"
            ),
        ),
        Index("ix_workflow_runs_alert_created", "alert_id", "created_at"),
        Index("ix_workflow_runs_status_updated", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="RESTRICT")
    )
    thread_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    workflow_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[WorkflowRunStatus] = mapped_column(
        String(32),
        default=WorkflowRunStatus.QUEUED,
        server_default=WorkflowRunStatus.QUEUED.value,
    )
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    alert: Mapped[Alert] = relationship(back_populates="workflow_runs")
    events: Mapped[list[WorkflowEvent]] = relationship(back_populates="workflow_run")
    tool_executions: Mapped[list[ToolExecution]] = relationship(back_populates="workflow_run")
    diagnosis_reports: Mapped[list[DiagnosisReport]] = relationship(back_populates="workflow_run")


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "sequence", name="workflow_events_run_sequence"),
        UniqueConstraint(
            "workflow_run_id",
            "idempotency_key",
            name="workflow_events_run_idempotency_key",
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "event_type IN ('workflow_queued', 'workflow_started', 'node_started', "
            "'node_completed', 'node_failed', 'tool_started', 'tool_completed', "
            "'tool_failed', 'human_input_required', 'human_decision_received', "
            "'workflow_completed', 'workflow_rejected', 'workflow_failed')",
            name="event_type_values",
        ),
        Index("ix_workflow_events_run_occurred", "workflow_run_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="RESTRICT")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[WorkflowEventType] = mapped_column(String(48))
    node_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="events")
