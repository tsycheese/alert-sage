from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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
from app.models.enums import RagEvaluationRunStatus, RagEvaluationSplit


class RagEvaluationRun(Base):
    __tablename__ = "rag_evaluation_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="rag_evaluation_runs_idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="status_values",
        ),
        CheckConstraint(
            "split IN ('calibration', 'test')",
            name="split_values",
        ),
        CheckConstraint(
            "provider IN ('mock', 'dify')",
            name="provider_values",
        ),
        CheckConstraint("top_k BETWEEN 1 AND 10", name="top_k_range"),
        CheckConstraint(
            "score_threshold IS NULL OR (score_threshold >= 0 AND score_threshold <= 1)",
            name="score_threshold_range",
        ),
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        CheckConstraint("query_count >= 0", name="query_count_non_negative"),
        CheckConstraint(
            "completed_query_count >= 0 AND completed_query_count <= query_count",
            name="completed_query_count_range",
        ),
        Index("ix_rag_evaluation_runs_status_created", "status", "created_at"),
        Index("ix_rag_evaluation_runs_set_version", "evaluation_set_id", "evaluation_set_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    evaluation_set_id: Mapped[str] = mapped_column(String(80))
    evaluation_set_version: Mapped[str] = mapped_column(String(40))
    evaluation_set_sha256: Mapped[str] = mapped_column(String(64))
    build_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(24))
    dataset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    split: Mapped[RagEvaluationSplit] = mapped_column(String(16))
    top_k: Mapped[int] = mapped_column(Integer)
    score_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[RagEvaluationRunStatus] = mapped_column(
        String(16),
        default=RagEvaluationRunStatus.QUEUED,
        server_default=RagEvaluationRunStatus.QUEUED.value,
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    query_count: Mapped[int] = mapped_column(Integer)
    completed_query_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    summary_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    results: Mapped[list[RagEvaluationResult]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RagEvaluationResult.query_id",
    )


class RagEvaluationResult(Base):
    __tablename__ = "rag_evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "query_id", name="rag_evaluation_results_run_query"),
        CheckConstraint(
            "split IN ('calibration', 'test')",
            name="split_values",
        ),
        CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="difficulty_values",
        ),
        CheckConstraint(
            "recall_at_k IS NULL OR (recall_at_k >= 0 AND recall_at_k <= 1)",
            name="recall_range",
        ),
        CheckConstraint(
            "reciprocal_rank IS NULL OR (reciprocal_rank >= 0 AND reciprocal_rank <= 1)",
            name="reciprocal_rank_range",
        ),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        Index("ix_rag_evaluation_results_run_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_evaluation_runs.id", ondelete="CASCADE"),
    )
    query_id: Mapped[str] = mapped_column(String(80))
    split: Mapped[RagEvaluationSplit] = mapped_column(String(16))
    difficulty: Mapped[str] = mapped_column(String(16))
    query_text: Mapped[str] = mapped_column(String(250))
    ground_truth: Mapped[dict[str, Any]] = mapped_column(JSONB)
    retrieved_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    source_hit_at_k: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recall_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    reciprocal_rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    abstention_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    false_positive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[RagEvaluationRun] = relationship(back_populates="results")
