from typing import Any

__all__ = [
    "AlertWorkflowService",
    "AlertWorkflowState",
    "build_alert_graph",
    "open_alert_workflow_service",
]


def __getattr__(name: str) -> Any:
    if name == "open_alert_workflow_service":
        from app.workflows.alert.checkpoint import open_alert_workflow_service

        return open_alert_workflow_service
    if name == "build_alert_graph":
        from app.workflows.alert.graph import build_alert_graph

        return build_alert_graph
    if name == "AlertWorkflowService":
        from app.workflows.alert.service import AlertWorkflowService

        return AlertWorkflowService
    if name == "AlertWorkflowState":
        from app.workflows.alert.state import AlertWorkflowState

        return AlertWorkflowState
    raise AttributeError(name)
