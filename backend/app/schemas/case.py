from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import KnowledgeSyncStatus


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    diagnosis_report_id: UUID
    title: str
    symptom: str
    root_cause: str
    resolution: str
    evidence: list[dict[str, Any]]
    tags: list[str]
    knowledge_sync_status: KnowledgeSyncStatus
    knowledge_sync_attempt: int
    external_document_id: str | None
    sync_error_code: str | None
    sync_error_message: str | None
    synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CaseSyncAcceptedResponse(BaseModel):
    case_id: UUID
    status: KnowledgeSyncStatus
    dispatched: bool
