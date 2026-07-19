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
from app.models.enums import KnowledgeSyncStatus

if TYPE_CHECKING:
    from app.models.diagnosis import DiagnosisReport


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("diagnosis_report_id", name="cases_diagnosis_report"),
        CheckConstraint(
            "knowledge_sync_status IN ('pending', 'syncing', 'synced', 'failed')",
            name="knowledge_sync_status_values",
        ),
        CheckConstraint("knowledge_sync_attempt >= 0", name="sync_attempt_non_negative"),
        Index("ix_cases_sync_status_updated", "knowledge_sync_status", "updated_at"),
        Index("ix_cases_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diagnosis_report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diagnosis_reports.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(255))
    symptom: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    knowledge_sync_status: Mapped[KnowledgeSyncStatus] = mapped_column(
        String(24),
        default=KnowledgeSyncStatus.PENDING,
        server_default=KnowledgeSyncStatus.PENDING.value,
    )
    knowledge_sync_attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    external_document_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sync_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sync_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    diagnosis_report: Mapped[DiagnosisReport] = relationship(back_populates="case")
