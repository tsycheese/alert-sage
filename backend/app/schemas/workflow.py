from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import HumanDecisionAction

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StableKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=8, max_length=160),
]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StableKey
    type: Literal["metric", "log", "cmdb", "knowledge", "case"]
    source: NonEmptyText
    title: NonEmptyText
    content: NonEmptyText
    reference: str | None = None
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class RootCauseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyText
    explanation: NonEmptyText
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence_refs: list[StableKey] = Field(min_length=1)


class RecommendationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyText
    rationale: NonEmptyText
    risk: Literal["low", "medium", "high"]
    actions: list[NonEmptyText] = Field(min_length=1)


class DiagnosisReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    summary: NonEmptyText
    root_causes: list[RootCauseItem] = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=1)
    recommendations: list[RecommendationItem] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    model_name: NonEmptyText
    prompt_version: NonEmptyText

    @model_validator(mode="after")
    def require_resolvable_evidence(self) -> "DiagnosisReportPayload":
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        missing = {
            reference
            for root_cause in self.root_causes
            for reference in root_cause.evidence_refs
            if reference not in evidence_ids
        }
        if missing:
            raise ValueError(f"unknown evidence references: {', '.join(sorted(missing))}")
        return self


class DiagnosisDraftPayload(BaseModel):
    """Structured model output before recommendations are generated."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    summary: NonEmptyText
    root_causes: list[RootCauseItem] = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    model_name: NonEmptyText
    prompt_version: NonEmptyText

    @model_validator(mode="after")
    def require_resolvable_evidence(self) -> "DiagnosisDraftPayload":
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        missing = {
            reference
            for root_cause in self.root_causes
            for reference in root_cause.evidence_refs
            if reference not in evidence_ids
        }
        if missing:
            raise ValueError(f"unknown evidence references: {', '.join(sorted(missing))}")
        return self


class HumanDecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: StableKey
    action: HumanDecisionAction
    comment: str | None = None

    @model_validator(mode="after")
    def require_reanalysis_feedback(self) -> "HumanDecisionCommand":
        if self.comment is not None:
            self.comment = self.comment.strip() or None
        if self.action is HumanDecisionAction.REANALYZE and self.comment is None:
            raise ValueError("comment is required when action is reanalyze")
        return self


class WorkflowResumePayload(HumanDecisionCommand):
    actor: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]
