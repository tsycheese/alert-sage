from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WorkflowStartMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID


class WorkflowResumeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID
    decision_id: UUID


class WorkflowRetryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID


class CaseSyncMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID


class RagEvaluationRunMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rag_evaluation_run_id: UUID
