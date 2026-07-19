from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from app.models.enums import HumanDecisionAction

StateName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class ContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: StateName
    status: Literal["succeeded", "partial", "failed"]
    data: dict[str, JsonValue] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    collected_at: datetime


class WorkflowToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_name: StateName
    tool_name: StateName
    code: StateName
    message: str
    retryable: bool
    attempts: int = Field(ge=1)


class WorkflowHumanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: HumanDecisionAction
    comment: str | None = None
    actor: StateName


class AlertWorkflowState(BaseModel):
    """Serializable business state passed between future LangGraph nodes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    alert_id: UUID
    workflow_run_id: UUID
    thread_id: StateName
    alert: dict[str, JsonValue]
    classification: dict[str, JsonValue] | None = None
    contexts: dict[str, ContextSnapshot] = Field(default_factory=dict)
    tool_errors: list[WorkflowToolError] = Field(default_factory=list)
    diagnosis: dict[str, JsonValue] | None = None
    recommendations: list[dict[str, JsonValue]] = Field(default_factory=list)
    report_id: UUID | None = None
    report_version: int = Field(default=0, ge=0)
    human_decision: WorkflowHumanDecision | None = None
    reanalysis_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
