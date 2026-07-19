from typing import Protocol
from uuid import UUID


class WorkflowDispatcher(Protocol):
    def start(self, workflow_run_id: UUID) -> None: ...

    def resume(self, workflow_run_id: UUID, decision_id: UUID) -> None: ...

    def retry(self, workflow_run_id: UUID) -> None: ...


class CaseDispatcher(Protocol):
    def sync(self, case_id: UUID) -> None: ...


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


class CeleryCaseDispatcher:
    def sync(self, case_id: UUID) -> None:
        from app.tasks.cases import run_case_sync

        run_case_sync.apply_async(
            args=[str(case_id)],
            task_id=f"case-sync-{case_id}",
        )


def get_workflow_dispatcher() -> WorkflowDispatcher:
    return CeleryWorkflowDispatcher()


def get_case_dispatcher() -> CaseDispatcher:
    return CeleryCaseDispatcher()
