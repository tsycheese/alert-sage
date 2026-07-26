from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import RagEvaluationRunStatus, RagEvaluationSplit

IdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=8, max_length=160),
]


class RagEvaluationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: IdempotencyKey
    split: RagEvaluationSplit = RagEvaluationSplit.CALIBRATION
    top_k: int = Field(default=3, ge=1, le=10)
    score_threshold: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    confirm_test_set: bool = False

    @model_validator(mode="after")
    def require_explicit_test_confirmation(self) -> "RagEvaluationRunCreate":
        if self.split is RagEvaluationSplit.TEST and not self.confirm_test_set:
            raise ValueError("test split requires confirm_test_set=true")
        return self


class RagEvaluationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    idempotency_key: str
    evaluation_set_id: str
    evaluation_set_version: str
    evaluation_set_sha256: str
    build_revision: str | None
    provider: str
    dataset_id: str | None
    split: RagEvaluationSplit
    top_k: int
    score_threshold: float | None
    status: RagEvaluationRunStatus
    attempt: int
    query_count: int
    completed_query_count: int
    summary_metrics: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RagEvaluationAcceptedResponse(BaseModel):
    run: RagEvaluationRunResponse
    dispatched: bool = Field(
        description="Whether this request created a new durable Outbox delivery intent."
    )


class RagEvaluationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    query_id: str
    split: RagEvaluationSplit
    difficulty: str
    query_text: str
    ground_truth: dict[str, Any]
    retrieved_items: list[dict[str, Any]]
    source_hit_at_k: bool | None
    recall_at_k: float | None
    reciprocal_rank: float | None
    abstention_correct: bool | None
    false_positive: bool | None
    latency_ms: float
    error_code: str | None
    created_at: datetime


class RagEvaluationDetailResponse(BaseModel):
    run: RagEvaluationRunResponse
    results: list[RagEvaluationResultResponse]


class RagEvaluationRunListResponse(BaseModel):
    items: list[RagEvaluationRunResponse]
    total: int
    page: int
    page_size: int
    pages: int
