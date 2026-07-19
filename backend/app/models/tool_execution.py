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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ToolExecutionStatus

if TYPE_CHECKING:
    from app.models.workflow import WorkflowRun


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "idempotency_key",
            name="tool_executions_run_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'timed_out', 'skipped')",
            name="status_values",
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="valid_time_range",
        ),
        Index("ix_tool_executions_run_tool", "workflow_run_id", "tool_name"),
        Index("ix_tool_executions_status_updated", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="RESTRICT")
    )
    node_name: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[ToolExecutionStatus] = mapped_column(
        String(24),
        default=ToolExecutionStatus.PENDING,
        server_default=ToolExecutionStatus.PENDING.value,
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    input_payload: Mapped[dict[str, Any]] = mapped_column("input", JSONB)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column("output", JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="tool_executions")
