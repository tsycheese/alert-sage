from collections.abc import Mapping

from app.models.enums import AlertStatus, WorkflowRunStatus

ALERT_STATUS_TRANSITIONS: Mapping[AlertStatus, frozenset[AlertStatus]] = {
    AlertStatus.RECEIVED: frozenset({AlertStatus.RUNNING, AlertStatus.FAILED}),
    AlertStatus.RUNNING: frozenset({AlertStatus.WAITING_FOR_APPROVAL, AlertStatus.FAILED}),
    AlertStatus.WAITING_FOR_APPROVAL: frozenset(
        {
            AlertStatus.REANALYZING,
            AlertStatus.COMPLETED,
            AlertStatus.REJECTED,
            AlertStatus.FAILED,
        }
    ),
    AlertStatus.REANALYZING: frozenset({AlertStatus.WAITING_FOR_APPROVAL, AlertStatus.FAILED}),
    AlertStatus.FAILED: frozenset({AlertStatus.RUNNING}),
    AlertStatus.COMPLETED: frozenset(),
    AlertStatus.REJECTED: frozenset(),
}

WORKFLOW_RUN_STATUS_TRANSITIONS: Mapping[WorkflowRunStatus, frozenset[WorkflowRunStatus]] = {
    WorkflowRunStatus.QUEUED: frozenset({WorkflowRunStatus.RUNNING, WorkflowRunStatus.FAILED}),
    WorkflowRunStatus.RUNNING: frozenset(
        {WorkflowRunStatus.WAITING_FOR_APPROVAL, WorkflowRunStatus.FAILED}
    ),
    WorkflowRunStatus.WAITING_FOR_APPROVAL: frozenset(
        {
            WorkflowRunStatus.REANALYZING,
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.REJECTED,
            WorkflowRunStatus.FAILED,
        }
    ),
    WorkflowRunStatus.REANALYZING: frozenset(
        {WorkflowRunStatus.WAITING_FOR_APPROVAL, WorkflowRunStatus.FAILED}
    ),
    WorkflowRunStatus.FAILED: frozenset({WorkflowRunStatus.QUEUED}),
    WorkflowRunStatus.COMPLETED: frozenset(),
    WorkflowRunStatus.REJECTED: frozenset(),
}


class InvalidStatusTransition(ValueError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"invalid status transition: {current} -> {target}")
        self.current = current
        self.target = target


def ensure_alert_status_transition(current: AlertStatus, target: AlertStatus) -> None:
    if current == target:
        return
    if target not in ALERT_STATUS_TRANSITIONS[current]:
        raise InvalidStatusTransition(current, target)


def ensure_workflow_run_status_transition(
    current: WorkflowRunStatus,
    target: WorkflowRunStatus,
) -> None:
    if current == target:
        return
    if target not in WORKFLOW_RUN_STATUS_TRANSITIONS[current]:
        raise InvalidStatusTransition(current, target)
