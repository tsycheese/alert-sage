from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StableKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    ),
]


class EvaluationDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: StableKey
    case_id: UUID
    title: NonEmptyText
    symptom: NonEmptyText
    root_cause: NonEmptyText
    resolution: NonEmptyText
    tags: list[StableKey] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_tags(self) -> "EvaluationDocument":
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("document tags must be unique")
        return self


class EvaluationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StableKey
    split: Literal["calibration", "test"]
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=250)]
    relevant_case_ids: list[UUID]
    expected_answer_points: list[NonEmptyText]
    tags: list[StableKey] = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"]
    should_abstain: bool = False

    @model_validator(mode="after")
    def validate_ground_truth(self) -> "EvaluationQuery":
        if len(self.relevant_case_ids) != len(set(self.relevant_case_ids)):
            raise ValueError("relevant case ids must be unique")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("query tags must be unique")
        if self.should_abstain:
            if self.relevant_case_ids or self.expected_answer_points:
                raise ValueError("abstention queries cannot define relevant cases or answer points")
        elif not self.relevant_case_ids or not self.expected_answer_points:
            raise ValueError("answerable queries require relevant cases and answer points")
        return self


class RagEvaluationSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    id: StableKey
    version: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=40,
            pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$",
        ),
    ]
    name: NonEmptyText
    description: NonEmptyText
    documents: list[EvaluationDocument] = Field(min_length=1)
    queries: list[EvaluationQuery] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_references_and_splits(self) -> "RagEvaluationSet":
        document_keys = [document.key for document in self.documents]
        if len(document_keys) != len(set(document_keys)):
            raise ValueError("document keys must be unique")
        case_ids = [document.case_id for document in self.documents]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("document case ids must be unique")

        query_ids = [query.id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query ids must be unique")
        if {query.split for query in self.queries} != {"calibration", "test"}:
            raise ValueError("evaluation set must contain calibration and test queries")

        known_case_ids = set(case_ids)
        missing = {
            case_id
            for query in self.queries
            for case_id in query.relevant_case_ids
            if case_id not in known_case_ids
        }
        if missing:
            raise ValueError(
                "queries reference unknown case ids: "
                + ", ".join(str(case_id) for case_id in sorted(missing, key=str))
            )
        return self


class RetrievalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: StableKey
    retrieved_case_ids: list[UUID] = Field(max_length=100)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    error_code: StableKey | None = None

    @model_validator(mode="after")
    def reject_results_on_error(self) -> "RetrievalObservation":
        if self.error_code is not None and self.retrieved_case_ids:
            raise ValueError("failed retrieval observations cannot contain results")
        return self


class QueryEvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: StableKey
    split: Literal["calibration", "test"]
    difficulty: Literal["easy", "medium", "hard"]
    answerable: bool
    source_hit_at_k: bool | None
    recall_at_k: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    reciprocal_rank: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    abstention_correct: bool | None
    false_positive: bool | None
    error: bool
    latency_ms: float = Field(ge=0, allow_inf_nan=False)


class AggregateEvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_count: int = Field(ge=0)
    answerable_count: int = Field(ge=0)
    abstention_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    source_hit_rate_at_k: float | None = Field(default=None, ge=0, le=1)
    recall_at_k: float | None = Field(default=None, ge=0, le=1)
    mean_reciprocal_rank: float | None = Field(default=None, ge=0, le=1)
    abstention_accuracy: float | None = Field(default=None, ge=0, le=1)
    no_answer_false_positive_rate: float | None = Field(default=None, ge=0, le=1)
    error_rate: float = Field(ge=0, le=1)
    latency_p50_ms: float | None = Field(default=None, ge=0)
    latency_p95_ms: float | None = Field(default=None, ge=0)


class RagEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_set_id: StableKey
    evaluation_set_version: str
    top_k: int = Field(ge=1, le=10)
    overall: AggregateEvaluationMetrics
    by_split: dict[Literal["calibration", "test"], AggregateEvaluationMetrics]
    queries: list[QueryEvaluationMetrics]
