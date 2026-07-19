from typing import Protocol
from uuid import UUID


class WorkflowDispatcher(Protocol):
    def start(self, workflow_run_id: UUID) -> None: ...

    def resume(self, workflow_run_id: UUID, decision_id: UUID) -> None: ...

    def retry(self, workflow_run_id: UUID) -> None: ...


class CeleryWorkflowDispatcher:
    def start(self, workflow_run_id: UUID) -> None:
        from app.tasks.workflows import run_workflow_start

        run_workflow_start.apply_async(
            args=[str(workflow_run_id)],
            task_id=f"workflow-start-{workflow_run_id}",
        )

    def resume(self, workflow_run_id: UUID, decision_id: UUID) -> None:
        from app.tasks.workflows import run_workflow_resume

        run_workflow_resume.apply_async(
            args=[str(workflow_run_id), str(decision_id)],
            task_id=f"workflow-resume-{decision_id}",
        )

    def retry(self, workflow_run_id: UUID) -> None:
        from app.tasks.workflows import run_workflow_retry

        run_workflow_retry.apply_async(args=[str(workflow_run_id)])


def get_workflow_dispatcher() -> WorkflowDispatcher:
    return CeleryWorkflowDispatcher()
