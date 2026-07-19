from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import HumanDecisionAction

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.workflow import WorkflowRun


class DiagnosisReport(Base):
    __tablename__ = "diagnosis_reports"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "version", name="diagnosis_reports_run_version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_diagnosis_reports_run_created", "workflow_run_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0", server_default="1.0")
    summary: Mapped[str] = mapped_column(Text)
    root_causes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    model_name: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="diagnosis_reports")
    human_decision: Mapped[HumanDecision | None] = relationship(back_populates="diagnosis_report")
    case: Mapped[Case | None] = relationship(back_populates="diagnosis_report")


class HumanDecision(Base):
    __tablename__ = "human_decisions"
    __table_args__ = (
        UniqueConstraint("diagnosis_report_id", name="human_decisions_diagnosis_report"),
        UniqueConstraint("idempotency_key", name="human_decisions_idempotency_key"),
        CheckConstraint(
            "action IN ('approve', 'reject', 'reanalyze')",
            name="action_values",
        ),
        CheckConstraint(
            "action <> 'reanalyze' OR (comment IS NOT NULL AND btrim(comment) <> '')",
            name="reanalyze_comment_required",
        ),
        Index("ix_human_decisions_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diagnosis_report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diagnosis_reports.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(160))
    action: Mapped[HumanDecisionAction] = mapped_column(String(24))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    diagnosis_report: Mapped[DiagnosisReport] = relationship(back_populates="human_decision")
