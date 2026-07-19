from enum import StrEnum


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    RECEIVED = "received"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    REANALYZING = "reanalyzing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class WorkflowRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    REANALYZING = "reanalyzing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class WorkflowEventType(StrEnum):
    WORKFLOW_QUEUED = "workflow_queued"
    WORKFLOW_STARTED = "workflow_started"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    HUMAN_INPUT_REQUIRED = "human_input_required"
    HUMAN_DECISION_RECEIVED = "human_decision_received"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_REJECTED = "workflow_rejected"
    WORKFLOW_FAILED = "workflow_failed"


class ToolExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


class HumanDecisionAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REANALYZE = "reanalyze"
