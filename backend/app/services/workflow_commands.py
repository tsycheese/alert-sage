import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.integrations.feishu.coordination import (
    schedule_card_sync,
    schedule_private_reminder_resolutions,
)
from app.models.alert import Alert
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.enums import (
    AlertStatus,
    HumanDecisionAction,
    OutboxTopic,
    WorkflowEventType,
    WorkflowRunStatus,
)
from app.models.workflow import WorkflowRun
from app.schemas.workflow import WorkflowResumePayload
from app.services.outbox import enqueue_outbox_message
from app.services.workflow_events import append_workflow_event
from app.workflows.alert.transitions import (
    ensure_alert_status_transition,
    ensure_workflow_run_status_transition,
)

WORKFLOW_VERSION = "1.0"
ACTIVE_STATUSES = {
    WorkflowRunStatus.QUEUED,
    WorkflowRunStatus.RUNNING,
    WorkflowRunStatus.WAITING_FOR_APPROVAL,
    WorkflowRunStatus.REANALYZING,
}


class WorkflowCommandNotFoundError(Exception):
    pass


class WorkflowCommandStateConflictError(Exception):
    pass


class WorkflowCommandIdempotencyConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PreparedStart:
    run: WorkflowRun
    alert: Alert
    created: bool
    delivery_created: bool


@dataclass(frozen=True, slots=True)
class PreparedDecision:
    should_resume: bool
    decision_id: UUID | None
    delivery_created: bool


class WorkflowCommandService:
    """Prepare durable workflow commands inside a caller-owned PostgreSQL transaction."""

    def __init__(self, session: AsyncSession, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def prepare_start(
        self,
        *,
        alert_id: UUID,
        idempotency_key: str,
        enqueue: bool = True,
    ) -> PreparedStart:
        existing = await self.session.scalar(
            select(WorkflowRun).where(WorkflowRun.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.alert_id != alert_id:
                raise WorkflowCommandIdempotencyConflictError
            alert = await self.session.get(Alert, alert_id)
            if alert is None:
                raise WorkflowCommandStateConflictError("workflow alert no longer exists")
            return PreparedStart(existing, alert, False, False)

        alert = await self.session.scalar(
            select(Alert).where(Alert.id == alert_id).with_for_update()
        )
        if alert is None:
            raise WorkflowCommandNotFoundError
        active = await self.session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.alert_id == alert_id, WorkflowRun.status.in_(ACTIVE_STATUSES))
            .order_by(WorkflowRun.created_at.desc())
            .limit(1)
        )
        if active is not None:
            return PreparedStart(active, alert, False, False)
        if alert.status != AlertStatus.RECEIVED:
            raise WorkflowCommandStateConflictError(
                f"alert status {alert.status} cannot start a new workflow"
            )
        run = WorkflowRun(
            id=uuid.uuid4(),
            alert_id=alert.id,
            thread_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            workflow_version=WORKFLOW_VERSION,
            status=WorkflowRunStatus.QUEUED,
        )
        self.session.add(run)
        await self.session.flush()
        await append_workflow_event(
            self.session,
            run,
            idempotency_key="workflow:queued:attempt-1",
            event_type=WorkflowEventType.WORKFLOW_QUEUED,
            status=WorkflowRunStatus.QUEUED,
            payload={"workflow_version": WORKFLOW_VERSION, "attempt": 1},
        )
        delivery_created = False
        if enqueue:
            _, delivery_created = await enqueue_outbox_message(
                self.session,
                topic=OutboxTopic.WORKFLOW_START,
                aggregate_id=run.id,
                idempotency_key=f"workflow:start:{run.id}",
                payload={"workflow_run_id": str(run.id)},
                correlation={
                    "alert_id": alert.id,
                    "workflow_run_id": run.id,
                    "thread_id": run.thread_id,
                },
            )
        await schedule_card_sync(
            self.session,
            alert_id=alert.id,
            reason=f"workflow-queued-{run.id}",
            settings=self.settings,
        )
        return PreparedStart(run, alert, True, delivery_created)

    async def prepare_retry(self, run_id: UUID, *, enqueue: bool = True) -> bool:
        run = await self._locked_run(run_id)
        if run.status == WorkflowRunStatus.QUEUED:
            return False
        if run.status != WorkflowRunStatus.FAILED:
            raise WorkflowCommandStateConflictError(
                f"workflow status {run.status} cannot be retried"
            )
        ensure_workflow_run_status_transition(run.status, WorkflowRunStatus.QUEUED)
        run.status = WorkflowRunStatus.QUEUED
        run.attempt += 1
        run.error_code = None
        run.error_message = None
        run.finished_at = None
        await append_workflow_event(
            self.session,
            run,
            idempotency_key=f"workflow:queued:attempt-{run.attempt}",
            event_type=WorkflowEventType.WORKFLOW_QUEUED,
            status=WorkflowRunStatus.QUEUED,
            payload={"workflow_version": run.workflow_version, "attempt": run.attempt},
        )
        delivery_created = True
        if enqueue:
            _, delivery_created = await enqueue_outbox_message(
                self.session,
                topic=OutboxTopic.WORKFLOW_RETRY,
                aggregate_id=run.id,
                idempotency_key=f"workflow:retry:{run.id}:attempt-{run.attempt}",
                payload={"workflow_run_id": str(run.id)},
                correlation={
                    "alert_id": run.alert_id,
                    "workflow_run_id": run.id,
                    "thread_id": run.thread_id,
                },
            )
        await schedule_card_sync(
            self.session,
            alert_id=run.alert_id,
            reason=f"workflow-retry-attempt-{run.attempt}",
            settings=self.settings,
        )
        return delivery_created

    async def prepare_decision(
        self,
        *,
        workflow_run_id: UUID,
        resume: WorkflowResumePayload,
        enqueue: bool = True,
    ) -> PreparedDecision:
        run = await self._locked_run(workflow_run_id)
        if run.status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.REJECTED}:
            existing = await self.session.scalar(
                select(HumanDecision).where(HumanDecision.idempotency_key == resume.idempotency_key)
            )
            if existing is None:
                raise WorkflowCommandStateConflictError("workflow is already terminal")
            self._ensure_same_decision(existing, resume)
            return PreparedDecision(False, existing.id, False)
        if run.status != WorkflowRunStatus.WAITING_FOR_APPROVAL:
            raise WorkflowCommandStateConflictError(
                f"workflow status {run.status} does not accept human input"
            )
        report = await self.session.scalar(
            select(DiagnosisReport)
            .where(DiagnosisReport.workflow_run_id == run.id)
            .order_by(DiagnosisReport.version.desc())
            .limit(1)
            .with_for_update()
        )
        if report is None:
            raise WorkflowCommandStateConflictError("workflow has no diagnosis report")
        existing = await self.session.scalar(
            select(HumanDecision).where(HumanDecision.idempotency_key == resume.idempotency_key)
        )
        if existing is not None:
            self._ensure_same_decision(existing, resume)
            if existing.diagnosis_report_id != report.id:
                return PreparedDecision(False, existing.id, False)
        else:
            report_decision = await self.session.scalar(
                select(HumanDecision).where(HumanDecision.diagnosis_report_id == report.id)
            )
            if report_decision is not None:
                raise WorkflowCommandStateConflictError(
                    "diagnosis report already has a human decision"
                )
            if resume.action is HumanDecisionAction.REANALYZE:
                count = await self.session.scalar(
                    select(func.count())
                    .select_from(HumanDecision)
                    .join(DiagnosisReport)
                    .where(
                        DiagnosisReport.workflow_run_id == run.id,
                        HumanDecision.action == HumanDecisionAction.REANALYZE,
                    )
                )
                if int(count or 0) >= 3:
                    raise WorkflowCommandStateConflictError(
                        "workflow reached the maximum of 3 reanalysis decisions"
                    )
            existing = HumanDecision(
                diagnosis_report_id=report.id,
                idempotency_key=resume.idempotency_key,
                action=resume.action,
                comment=resume.comment,
                actor=resume.actor,
                actor_source=resume.actor_source,
                actor_subject=resume.actor_subject,
                actor_display_name=resume.actor_display_name,
            )
            self.session.add(existing)
            await self.session.flush()
        await append_workflow_event(
            self.session,
            run,
            idempotency_key=f"human-decision:{resume.idempotency_key}",
            event_type=WorkflowEventType.HUMAN_DECISION_RECEIVED,
            node_name="human_review",
            status=run.status,
            payload={
                "action": resume.action.value,
                "report_version": report.version,
                "actor": resume.actor,
                "actor_source": resume.actor_source.value,
            },
        )
        if resume.action is HumanDecisionAction.REANALYZE:
            alert = await self._locked_alert(run.alert_id)
            ensure_workflow_run_status_transition(run.status, WorkflowRunStatus.REANALYZING)
            ensure_alert_status_transition(alert.status, AlertStatus.REANALYZING)
            run.status = WorkflowRunStatus.REANALYZING
            alert.status = AlertStatus.REANALYZING
        await schedule_private_reminder_resolutions(
            self.session,
            alert_id=run.alert_id,
            decision=existing,
            settings=self.settings,
        )
        await schedule_card_sync(
            self.session,
            alert_id=run.alert_id,
            reason=f"human-decision-{existing.id}",
            settings=self.settings,
        )
        delivery_created = False
        if enqueue:
            _, delivery_created = await enqueue_outbox_message(
                self.session,
                topic=OutboxTopic.WORKFLOW_RESUME,
                aggregate_id=run.id,
                idempotency_key=f"workflow:resume:{existing.id}",
                payload={
                    "workflow_run_id": str(run.id),
                    "decision_id": str(existing.id),
                },
                correlation={
                    "alert_id": run.alert_id,
                    "workflow_run_id": run.id,
                    "thread_id": run.thread_id,
                },
            )
        return PreparedDecision(True, existing.id, delivery_created)

    async def _locked_run(self, run_id: UUID) -> WorkflowRun:
        run = await self.session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
        )
        if run is None:
            raise WorkflowCommandNotFoundError
        return run

    async def _locked_alert(self, alert_id: UUID) -> Alert:
        alert = await self.session.scalar(
            select(Alert).where(Alert.id == alert_id).with_for_update()
        )
        if alert is None:
            raise WorkflowCommandStateConflictError("workflow alert no longer exists")
        return alert

    @staticmethod
    def _ensure_same_decision(
        existing: HumanDecision,
        resume: WorkflowResumePayload,
    ) -> None:
        if (
            existing.action != resume.action
            or existing.comment != resume.comment
            or existing.actor != resume.actor
            or existing.actor_source != resume.actor_source
            or existing.actor_subject != resume.actor_subject
            or existing.actor_display_name != resume.actor_display_name
        ):
            raise WorkflowCommandIdempotencyConflictError
