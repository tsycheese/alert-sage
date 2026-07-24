from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.alert import Alert
from app.models.case import Case
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.enums import (
    AlertStatus,
    HumanDecisionAction,
    ToolExecutionStatus,
    WorkflowEventType,
    WorkflowRunStatus,
)
from app.models.tool_execution import ToolExecution
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.schemas.workflow import (
    DiagnosisReportPayload,
    HumanDecisionCommand,
    WorkflowResumePayload,
)
from app.services.workflow_events import append_workflow_event
from app.workflows.alert.state import AlertWorkflowState
from app.workflows.alert.transitions import (
    ensure_alert_status_transition,
    ensure_workflow_run_status_transition,
)

WORKFLOW_VERSION = "1.0"


class WorkflowRunNotFoundError(Exception):
    pass


class WorkflowStateConflictError(Exception):
    pass


class WorkflowIdempotencyConflictError(Exception):
    pass


class WorkflowExecutionError(Exception):
    def __init__(self, workflow_run_id: UUID, cause: Exception) -> None:
        super().__init__(f"workflow {workflow_run_id} failed: {cause}")
        self.workflow_run_id = workflow_run_id
        self.cause = cause


@dataclass(frozen=True, slots=True)
class WorkflowExecutionResult:
    workflow_run_id: UUID
    thread_id: str
    status: WorkflowRunStatus
    current_node: str | None
    case_id: UUID | None
    state: AlertWorkflowState


@dataclass(frozen=True, slots=True)
class PreparedWorkflowRun:
    workflow_run_id: UUID
    alert_id: UUID
    thread_id: str
    created: bool


class AlertWorkflowService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], graph: Any) -> None:
        self.session_factory = session_factory
        self.graph = graph

    async def start(
        self,
        *,
        alert_id: UUID,
        idempotency_key: str,
    ) -> WorkflowExecutionResult:
        prepared = await self.prepare_start(
            alert_id=alert_id,
            idempotency_key=idempotency_key,
        )
        if not prepared.created:
            return await self.get_result(prepared.workflow_run_id)
        return await self.execute_start(prepared.workflow_run_id)

    async def prepare_start(
        self,
        *,
        alert_id: UUID,
        idempotency_key: str,
    ) -> PreparedWorkflowRun:
        run, _alert, created = await self._prepare_run(
            alert_id=alert_id,
            idempotency_key=idempotency_key,
        )
        return PreparedWorkflowRun(
            workflow_run_id=run.id,
            alert_id=run.alert_id,
            thread_id=run.thread_id,
            created=created,
        )

    async def execute_start(self, workflow_run_id: UUID) -> WorkflowExecutionResult:
        already_paused_or_terminal = False
        async with self.session_factory() as session:
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise WorkflowRunNotFoundError
            if run.status in {
                WorkflowRunStatus.WAITING_FOR_APPROVAL,
                WorkflowRunStatus.COMPLETED,
                WorkflowRunStatus.REJECTED,
            }:
                already_paused_or_terminal = True
            alert = await session.get(Alert, run.alert_id)
            if alert is None:
                raise WorkflowStateConflictError("workflow alert no longer exists")

        if already_paused_or_terminal:
            return await self.get_result(workflow_run_id)

        if run.status == WorkflowRunStatus.RUNNING:
            snapshot = await self.graph.aget_state(self._graph_config(run.thread_id))
            if snapshot.values:
                return await self._drive(workflow_run_id, None)

        await self._mark_started(run.id)
        initial_state = AlertWorkflowState(
            alert_id=alert.id,
            workflow_run_id=run.id,
            thread_id=run.thread_id,
            alert={
                "id": str(alert.id),
                "source": alert.source,
                "external_alert_id": alert.external_alert_id,
                "alert_name": alert.alert_name,
                "service": alert.service,
                "instance": alert.instance,
                "severity": str(alert.severity),
                "payload": alert.payload,
                "started_at": alert.started_at.isoformat(),
            },
        )
        return await self._drive(run.id, initial_state.model_dump(mode="json"))

    async def resume(
        self,
        *,
        workflow_run_id: UUID,
        command: HumanDecisionCommand,
        actor: str,
    ) -> WorkflowExecutionResult:
        resume = WorkflowResumePayload(
            idempotency_key=command.idempotency_key,
            action=command.action,
            comment=command.comment,
            actor=actor,
        )
        should_resume, _decision_id = await self._persist_human_decision(workflow_run_id, resume)
        if not should_resume:
            return await self.get_result(workflow_run_id)
        return await self._drive(
            workflow_run_id,
            Command(resume=resume.model_dump(mode="json")),
        )

    async def prepare_resume(
        self,
        *,
        workflow_run_id: UUID,
        command: HumanDecisionCommand,
        actor: str,
    ) -> UUID | None:
        resume = WorkflowResumePayload(
            idempotency_key=command.idempotency_key,
            action=command.action,
            comment=command.comment,
            actor=actor,
        )
        should_resume, decision_id = await self._persist_human_decision(workflow_run_id, resume)
        return decision_id if should_resume else None

    async def execute_resume(
        self,
        *,
        workflow_run_id: UUID,
        decision_id: UUID,
    ) -> WorkflowExecutionResult:
        already_terminal = False
        async with self.session_factory() as session:
            decision = await session.get(HumanDecision, decision_id)
            if decision is None:
                raise WorkflowStateConflictError("human decision does not exist")
            report = await session.get(DiagnosisReport, decision.diagnosis_report_id)
            if report is None or report.workflow_run_id != workflow_run_id:
                raise WorkflowStateConflictError("human decision does not belong to workflow")
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise WorkflowRunNotFoundError
            if run.status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.REJECTED}:
                already_terminal = True
            resume = WorkflowResumePayload(
                idempotency_key=decision.idempotency_key,
                action=decision.action,
                comment=decision.comment,
                actor=decision.actor,
            )
            thread_id = run.thread_id
        if already_terminal:
            return await self.get_result(workflow_run_id)
        snapshot = await self.graph.aget_state(self._graph_config(thread_id))
        graph_input: object = (
            Command(resume=resume.model_dump(mode="json"))
            if "human_review" in snapshot.next
            else None
        )
        return await self._drive(
            workflow_run_id,
            graph_input,
        )

    async def prepare_retry(self, workflow_run_id: UUID) -> None:
        async with self.session_factory() as session:
            run = await self._locked_run(session, workflow_run_id)
            if run.status == WorkflowRunStatus.QUEUED:
                return
            if run.status != WorkflowRunStatus.FAILED:
                raise WorkflowStateConflictError(f"workflow status {run.status} cannot be retried")
            ensure_workflow_run_status_transition(run.status, WorkflowRunStatus.QUEUED)
            run.status = WorkflowRunStatus.QUEUED
            run.attempt += 1
            run.error_code = None
            run.error_message = None
            run.finished_at = None
            await self._append_event(
                session,
                run,
                idempotency_key=f"workflow:queued:attempt-{run.attempt}",
                event_type=WorkflowEventType.WORKFLOW_QUEUED,
                status=WorkflowRunStatus.QUEUED,
                payload={"workflow_version": run.workflow_version, "attempt": run.attempt},
            )
            await session.commit()

    async def execute_retry(self, workflow_run_id: UUID) -> WorkflowExecutionResult:
        async with self.session_factory() as session:
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise WorkflowRunNotFoundError
            thread_id = run.thread_id
        snapshot = await self.graph.aget_state(self._graph_config(thread_id))
        if snapshot.values:
            await self._mark_started(workflow_run_id)
            return await self._drive(workflow_run_id, None)
        return await self.execute_start(workflow_run_id)

    async def mark_dispatch_failed(self, workflow_run_id: UUID, error: Exception) -> None:
        await self._mark_failed(workflow_run_id, error)

    async def get_result(self, workflow_run_id: UUID) -> WorkflowExecutionResult:
        async with self.session_factory() as session:
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise WorkflowRunNotFoundError
            thread_id = run.thread_id
            status = run.status
            current_node = run.current_node
            case_id = await session.scalar(
                select(Case.id)
                .join(DiagnosisReport, Case.diagnosis_report_id == DiagnosisReport.id)
                .where(DiagnosisReport.workflow_run_id == workflow_run_id)
                .order_by(Case.created_at.desc())
                .limit(1)
            )
        snapshot = await self.graph.aget_state(self._graph_config(thread_id))
        if not snapshot.values:
            raise WorkflowStateConflictError("workflow checkpoint does not exist")
        state = AlertWorkflowState.model_validate(snapshot.values)
        return WorkflowExecutionResult(
            workflow_run_id=workflow_run_id,
            thread_id=thread_id,
            status=status,
            current_node=current_node,
            case_id=case_id,
            state=state,
        )

    async def _prepare_run(
        self,
        *,
        alert_id: UUID,
        idempotency_key: str,
    ) -> tuple[WorkflowRun, Alert, bool]:
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.idempotency_key == idempotency_key)
            )
            if existing is not None:
                if existing.alert_id != alert_id:
                    raise WorkflowIdempotencyConflictError
                alert = await session.get(Alert, alert_id)
                if alert is None:
                    raise WorkflowStateConflictError("workflow alert no longer exists")
                return existing, alert, False

            alert = await session.scalar(
                select(Alert).where(Alert.id == alert_id).with_for_update()
            )
            if alert is None:
                raise WorkflowRunNotFoundError
            if alert.status not in {AlertStatus.RECEIVED, AlertStatus.FAILED}:
                raise WorkflowStateConflictError(
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
            session.add(run)
            await session.flush()
            await self._append_event(
                session,
                run,
                idempotency_key="workflow:queued:attempt-1",
                event_type=WorkflowEventType.WORKFLOW_QUEUED,
                status=WorkflowRunStatus.QUEUED,
                payload={"workflow_version": WORKFLOW_VERSION, "attempt": 1},
            )
            await session.commit()
            return run, alert, True

    async def _mark_started(self, workflow_run_id: UUID) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            run = await self._locked_run(session, workflow_run_id)
            if run.status == WorkflowRunStatus.RUNNING:
                return
            alert = await self._locked_alert(session, run.alert_id)
            ensure_workflow_run_status_transition(run.status, WorkflowRunStatus.RUNNING)
            ensure_alert_status_transition(alert.status, AlertStatus.RUNNING)
            run.status = WorkflowRunStatus.RUNNING
            run.started_at = run.started_at or now
            alert.status = AlertStatus.RUNNING
            await self._append_event(
                session,
                run,
                idempotency_key=f"workflow:started:attempt-{run.attempt}",
                event_type=WorkflowEventType.WORKFLOW_STARTED,
                status=WorkflowRunStatus.RUNNING,
                payload={"attempt": run.attempt},
            )
            await session.commit()

    async def _drive(self, workflow_run_id: UUID, graph_input: object) -> WorkflowExecutionResult:
        async with self.session_factory() as session:
            run = await session.get(WorkflowRun, workflow_run_id)
            if run is None:
                raise WorkflowRunNotFoundError
            config = self._graph_config(run.thread_id)
        try:
            async for chunk in self.graph.astream(graph_input, config, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    await self._mark_waiting(workflow_run_id)
                    continue
                for node_name, node_update in chunk.items():
                    await self._persist_node(
                        workflow_run_id,
                        node_name,
                        node_update,
                        config,
                    )
        except Exception as exc:
            await self._mark_failed(workflow_run_id, exc)
            raise WorkflowExecutionError(workflow_run_id, exc) from exc
        return await self.get_result(workflow_run_id)

    async def _persist_node(
        self,
        workflow_run_id: UUID,
        node_name: str,
        node_update: object,
        config: dict[str, dict[str, str]],
    ) -> None:
        snapshot = await self.graph.aget_state(config)
        state_values = dict(snapshot.values)
        if isinstance(node_update, dict):
            state_values.update(node_update)
        state = AlertWorkflowState.model_validate(state_values)
        async with self.session_factory() as session:
            run = await self._locked_run(session, workflow_run_id)
            alert = await self._locked_alert(session, run.alert_id)
            run.current_node = node_name
            event_version = (
                state.decision_idempotency_key
                or f"report-{state.report_version}:reanalysis-{state.reanalysis_count}"
            )
            await self._append_event(
                session,
                run,
                idempotency_key=f"node:{node_name}:completed:{event_version}",
                event_type=WorkflowEventType.NODE_COMPLETED,
                node_name=node_name,
                status=run.status,
                payload={
                    "report_version": state.report_version,
                    "reanalysis_count": state.reanalysis_count,
                },
            )
            if node_name == "collect_context":
                await self._persist_tools(session, run, state)
            elif node_name == "recommend":
                await self._persist_report(session, run, state)
            elif node_name == "finalize":
                await self._persist_final_status(session, run, alert, state)
            await session.commit()

    async def _persist_tools(
        self,
        session: AsyncSession,
        run: WorkflowRun,
        state: AlertWorkflowState,
    ) -> None:
        now = datetime.now(UTC)
        for name, context in state.contexts.items():
            key = f"collect-context:{name}:1"
            if await self._tool_exists(session, run.id, key):
                continue
            session.add(
                ToolExecution(
                    workflow_run_id=run.id,
                    node_name="collect_context",
                    tool_name=name,
                    idempotency_key=key,
                    status=ToolExecutionStatus.SUCCEEDED,
                    attempt=context.attempts,
                    input_payload={"service": state.alert.get("service")},
                    output_payload={
                        "status": context.status,
                        "data": context.data,
                        "source_refs": context.source_refs,
                    },
                    duration_ms=context.duration_ms,
                    started_at=now,
                    finished_at=now,
                )
            )
            await self._append_event(
                session,
                run,
                idempotency_key=f"tool:{name}:completed:1",
                event_type=WorkflowEventType.TOOL_COMPLETED,
                node_name="collect_context",
                status=ToolExecutionStatus.SUCCEEDED,
                payload={"tool_name": name, "attempts": context.attempts},
            )
        for error in state.tool_errors:
            key = f"collect-context:{error.tool_name}:1"
            if await self._tool_exists(session, run.id, key):
                continue
            session.add(
                ToolExecution(
                    workflow_run_id=run.id,
                    node_name=error.node_name,
                    tool_name=error.tool_name,
                    idempotency_key=key,
                    status=ToolExecutionStatus.FAILED,
                    attempt=error.attempts,
                    input_payload={"service": state.alert.get("service")},
                    error_code=error.code,
                    error_message=error.message,
                    duration_ms=error.duration_ms,
                    started_at=now,
                    finished_at=now,
                )
            )
            await self._append_event(
                session,
                run,
                idempotency_key=f"tool:{error.tool_name}:failed:1",
                event_type=WorkflowEventType.TOOL_FAILED,
                node_name="collect_context",
                status=ToolExecutionStatus.FAILED,
                payload={
                    "tool_name": error.tool_name,
                    "error_code": error.code,
                    "attempts": error.attempts,
                },
            )

    async def _persist_report(
        self,
        session: AsyncSession,
        run: WorkflowRun,
        state: AlertWorkflowState,
    ) -> None:
        if state.report_id is None or state.diagnosis is None:
            raise WorkflowStateConflictError("recommend node did not produce a report")
        existing = await session.get(DiagnosisReport, state.report_id)
        if existing is not None:
            return
        payload = DiagnosisReportPayload.model_validate(
            {**state.diagnosis, "recommendations": state.recommendations}
        )
        session.add(
            DiagnosisReport(
                id=state.report_id,
                workflow_run_id=run.id,
                version=state.report_version,
                schema_version=payload.schema_version,
                summary=payload.summary,
                root_causes=[item.model_dump(mode="json") for item in payload.root_causes],
                evidence=[item.model_dump(mode="json") for item in payload.evidence],
                recommendations=[item.model_dump(mode="json") for item in payload.recommendations],
                confidence=Decimal(str(payload.confidence)),
                model_name=payload.model_name,
                prompt_version=payload.prompt_version,
            )
        )
        await session.flush()

    async def _mark_waiting(self, workflow_run_id: UUID) -> None:
        async with self.session_factory() as session:
            run = await self._locked_run(session, workflow_run_id)
            alert = await self._locked_alert(session, run.alert_id)
            if run.status != WorkflowRunStatus.WAITING_FOR_APPROVAL:
                ensure_workflow_run_status_transition(
                    run.status, WorkflowRunStatus.WAITING_FOR_APPROVAL
                )
                ensure_alert_status_transition(alert.status, AlertStatus.WAITING_FOR_APPROVAL)
                run.status = WorkflowRunStatus.WAITING_FOR_APPROVAL
                alert.status = AlertStatus.WAITING_FOR_APPROVAL
            run.current_node = "human_review"
            latest_version = await session.scalar(
                select(func.max(DiagnosisReport.version)).where(
                    DiagnosisReport.workflow_run_id == run.id
                )
            )
            await self._append_event(
                session,
                run,
                idempotency_key=f"human-input:required:report-{latest_version or 0}",
                event_type=WorkflowEventType.HUMAN_INPUT_REQUIRED,
                node_name="human_review",
                status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
                payload={"report_version": latest_version or 0},
            )
            await session.commit()

    async def _persist_human_decision(
        self,
        workflow_run_id: UUID,
        resume: WorkflowResumePayload,
    ) -> tuple[bool, UUID | None]:
        async with self.session_factory() as session:
            run = await self._locked_run(session, workflow_run_id)
            if run.status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.REJECTED}:
                existing = await session.scalar(
                    select(HumanDecision).where(
                        HumanDecision.idempotency_key == resume.idempotency_key
                    )
                )
                if existing is None:
                    raise WorkflowStateConflictError("workflow is already terminal")
                self._ensure_same_decision(existing, resume)
                return False, existing.id
            if run.status != WorkflowRunStatus.WAITING_FOR_APPROVAL:
                raise WorkflowStateConflictError(
                    f"workflow status {run.status} does not accept human input"
                )

            report = await session.scalar(
                select(DiagnosisReport)
                .where(DiagnosisReport.workflow_run_id == run.id)
                .order_by(DiagnosisReport.version.desc())
                .limit(1)
                .with_for_update()
            )
            if report is None:
                raise WorkflowStateConflictError("workflow has no diagnosis report")
            existing = await session.scalar(
                select(HumanDecision).where(HumanDecision.idempotency_key == resume.idempotency_key)
            )
            if existing is not None:
                self._ensure_same_decision(existing, resume)
                if existing.diagnosis_report_id != report.id:
                    return False, existing.id
            else:
                report_decision = await session.scalar(
                    select(HumanDecision).where(HumanDecision.diagnosis_report_id == report.id)
                )
                if report_decision is not None:
                    raise WorkflowStateConflictError(
                        "diagnosis report already has a human decision"
                    )
                existing = HumanDecision(
                    diagnosis_report_id=report.id,
                    idempotency_key=resume.idempotency_key,
                    action=resume.action,
                    comment=resume.comment,
                    actor=resume.actor,
                )
                session.add(existing)
                await session.flush()

            await self._append_event(
                session,
                run,
                idempotency_key=f"human-decision:{resume.idempotency_key}",
                event_type=WorkflowEventType.HUMAN_DECISION_RECEIVED,
                node_name="human_review",
                status=run.status,
                payload={
                    "action": resume.action.value,
                    "report_version": report.version,
                    "actor": resume.actor,
                },
            )
            if resume.action is HumanDecisionAction.REANALYZE:
                alert = await self._locked_alert(session, run.alert_id)
                ensure_workflow_run_status_transition(run.status, WorkflowRunStatus.REANALYZING)
                ensure_alert_status_transition(alert.status, AlertStatus.REANALYZING)
                run.status = WorkflowRunStatus.REANALYZING
                alert.status = AlertStatus.REANALYZING
            await session.commit()
            return True, existing.id

    async def _persist_final_status(
        self,
        session: AsyncSession,
        run: WorkflowRun,
        alert: Alert,
        state: AlertWorkflowState,
    ) -> None:
        now = datetime.now(UTC)
        if state.final_status == "completed":
            run_target = WorkflowRunStatus.COMPLETED
            alert_target = AlertStatus.COMPLETED
            event_type = WorkflowEventType.WORKFLOW_COMPLETED
            await self._persist_case(session, run, alert, state)
        elif state.final_status == "rejected":
            run_target = WorkflowRunStatus.REJECTED
            alert_target = AlertStatus.REJECTED
            event_type = WorkflowEventType.WORKFLOW_REJECTED
        else:
            raise WorkflowStateConflictError("finalize node did not choose a terminal status")
        ensure_workflow_run_status_transition(run.status, run_target)
        ensure_alert_status_transition(alert.status, alert_target)
        run.status = run_target
        run.finished_at = now
        alert.status = alert_target
        await self._append_event(
            session,
            run,
            idempotency_key=f"workflow:{state.final_status}",
            event_type=event_type,
            node_name="finalize",
            status=run_target,
            payload={"report_version": state.report_version},
        )

    async def _persist_case(
        self,
        session: AsyncSession,
        run: WorkflowRun,
        alert: Alert,
        state: AlertWorkflowState,
    ) -> Case:
        if state.report_id is None:
            raise WorkflowStateConflictError("approved workflow has no diagnosis report")
        existing = await session.scalar(
            select(Case).where(Case.diagnosis_report_id == state.report_id)
        )
        if existing is not None:
            return existing
        report = await session.get(DiagnosisReport, state.report_id)
        if report is None:
            raise WorkflowStateConflictError("approved diagnosis report does not exist")
        root_cause = "\n".join(
            f"{item.get('title', 'Unknown cause')}: {item.get('explanation', '')}".strip()
            for item in report.root_causes
        )
        resolution_parts: list[str] = []
        for item in report.recommendations:
            actions = item.get("actions") or []
            action_text = "; ".join(str(action) for action in actions)
            resolution_parts.append(f"{item.get('title', 'Recommendation')}: {action_text}".strip())
        payload_summary = alert.payload.get("summary")
        symptom = (
            str(payload_summary)
            if payload_summary
            else f"{alert.alert_name} on {alert.instance or alert.service}"
        )
        case = Case(
            id=uuid.uuid5(report.id, "case"),
            diagnosis_report_id=report.id,
            title=f"{alert.service}: {alert.alert_name} 处置案例"[:255],
            symptom=symptom,
            root_cause=root_cause or report.summary,
            resolution="\n".join(resolution_parts) or report.summary,
            evidence=report.evidence,
            tags=list(dict.fromkeys([alert.service, alert.alert_name, str(alert.severity)])),
        )
        session.add(case)
        await session.flush()
        await self._append_event(
            session,
            run,
            idempotency_key=f"case:created:{case.id}",
            event_type=WorkflowEventType.CASE_CREATED,
            node_name="finalize",
            status=case.knowledge_sync_status,
            payload={"case_id": str(case.id), "report_version": report.version},
        )
        return case

    async def _mark_failed(self, workflow_run_id: UUID, error: Exception) -> None:
        async with self.session_factory() as session:
            run = await self._locked_run(session, workflow_run_id)
            if run.status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.REJECTED}:
                return
            alert = await self._locked_alert(session, run.alert_id)
            ensure_workflow_run_status_transition(run.status, WorkflowRunStatus.FAILED)
            ensure_alert_status_transition(alert.status, AlertStatus.FAILED)
            run.status = WorkflowRunStatus.FAILED
            run.error_code = type(error).__name__[:64]
            run.error_message = str(error)[:2000]
            run.finished_at = datetime.now(UTC)
            alert.status = AlertStatus.FAILED
            await self._append_event(
                session,
                run,
                idempotency_key=f"workflow:failed:attempt-{run.attempt}",
                event_type=WorkflowEventType.WORKFLOW_FAILED,
                status=WorkflowRunStatus.FAILED,
                payload={"error_code": run.error_code},
            )
            await session.commit()

    @staticmethod
    async def _append_event(
        session: AsyncSession,
        run: WorkflowRun,
        *,
        idempotency_key: str,
        event_type: WorkflowEventType,
        payload: dict[str, object],
        node_name: str | None = None,
        status: object | None = None,
    ) -> WorkflowEvent:
        return await append_workflow_event(
            session,
            run,
            idempotency_key=idempotency_key,
            event_type=event_type,
            payload=payload,
            node_name=node_name,
            status=status,
        )

    @staticmethod
    async def _tool_exists(session: AsyncSession, run_id: UUID, key: str) -> bool:
        return (
            await session.scalar(
                select(ToolExecution.id).where(
                    ToolExecution.workflow_run_id == run_id,
                    ToolExecution.idempotency_key == key,
                )
            )
            is not None
        )

    @staticmethod
    async def _locked_run(session: AsyncSession, workflow_run_id: UUID) -> WorkflowRun:
        run = await session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == workflow_run_id).with_for_update()
        )
        if run is None:
            raise WorkflowRunNotFoundError
        return run

    @staticmethod
    async def _locked_alert(session: AsyncSession, alert_id: UUID) -> Alert:
        alert = await session.scalar(select(Alert).where(Alert.id == alert_id).with_for_update())
        if alert is None:
            raise WorkflowStateConflictError("workflow alert no longer exists")
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
        ):
            raise WorkflowIdempotencyConflictError

    @staticmethod
    def _graph_config(thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}
