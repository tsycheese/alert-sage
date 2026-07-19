from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.workflow import WorkflowEvent, WorkflowRun


class AlertWorkflowNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowDetail:
    run: WorkflowRun
    report: DiagnosisReport | None
    decision: HumanDecision | None


class WorkflowQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def latest_run(self, alert_id: UUID) -> WorkflowRun:
        alert_exists = await self.session.scalar(select(Alert.id).where(Alert.id == alert_id))
        if alert_exists is None:
            raise AlertWorkflowNotFoundError("alert")
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.alert_id == alert_id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(1)
        )
        if run is None:
            raise AlertWorkflowNotFoundError("workflow")
        return run

    async def detail(self, alert_id: UUID) -> WorkflowDetail:
        run = await self.latest_run(alert_id)
        report = await self.session.scalar(
            select(DiagnosisReport)
            .where(DiagnosisReport.workflow_run_id == run.id)
            .order_by(DiagnosisReport.version.desc())
            .limit(1)
        )
        decision = None
        if report is not None:
            decision = await self.session.scalar(
                select(HumanDecision).where(HumanDecision.diagnosis_report_id == report.id)
            )
        return WorkflowDetail(run=run, report=report, decision=decision)

    async def events(
        self,
        alert_id: UUID,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> tuple[WorkflowRun, list[WorkflowEvent]]:
        run = await self.latest_run(alert_id)
        events = list(
            await self.session.scalars(
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.workflow_run_id == run.id,
                    WorkflowEvent.sequence > after,
                )
                .order_by(WorkflowEvent.sequence)
                .limit(limit)
            )
        )
        return run, events
