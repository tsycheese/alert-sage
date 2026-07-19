from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.alert import Alert
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    HumanDecisionAction,
    ToolExecutionStatus,
    WorkflowEventType,
    WorkflowRunStatus,
)
from app.models.tool_execution import ToolExecution
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.schemas.workflow import DiagnosisReportPayload, HumanDecisionCommand
from app.workflows.alert.state import AlertWorkflowState
from app.workflows.alert.transitions import (
    InvalidStatusTransition,
    ensure_alert_status_transition,
    ensure_workflow_run_status_transition,
)


async def create_alert(session: AsyncSession, external_alert_id: str = "workflow-alert") -> Alert:
    alert = Alert(
        source="web",
        external_alert_id=external_alert_id,
        fingerprint="a" * 64,
        alert_name="HighCPUUsage",
        service="order-service",
        instance="order-service-01",
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.RECEIVED,
        payload={"value": 92.5, "threshold": 80},
        started_at=datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
    )
    session.add(alert)
    await session.flush()
    return alert


def workflow_run(alert: Alert, suffix: str = "1", **values: object) -> WorkflowRun:
    return WorkflowRun(
        alert_id=alert.id,
        thread_id=f"thread-{suffix}",
        idempotency_key=f"start-workflow-{suffix}",
        workflow_version="1.0",
        **values,
    )


def report_payload() -> DiagnosisReportPayload:
    return DiagnosisReportPayload.model_validate(
        {
            "summary": "CPU saturation correlates with a slow database query.",
            "root_causes": [
                {
                    "title": "Slow database query",
                    "explanation": "A new query consumes most request time.",
                    "confidence": 0.91,
                    "evidence_refs": ["metric-cpu-01", "log-sql-01"],
                }
            ],
            "evidence": [
                {
                    "id": "metric-cpu-01",
                    "type": "metric",
                    "source": "mock-prometheus",
                    "title": "CPU increased",
                    "content": "CPU increased from 45% to 92%.",
                    "score": 0.96,
                },
                {
                    "id": "log-sql-01",
                    "type": "log",
                    "source": "mock-loki",
                    "title": "Slow query detected",
                    "content": "Query latency exceeded two seconds.",
                    "score": 0.89,
                },
            ],
            "recommendations": [
                {
                    "title": "Inspect the query plan",
                    "rationale": "Confirm the suspected database bottleneck.",
                    "risk": "low",
                    "actions": ["Run EXPLAIN ANALYZE in a read-only session"],
                }
            ],
            "confidence": 0.91,
            "model_name": "mock-diagnostic-model",
            "prompt_version": "diagnosis-v1",
        }
    )


def diagnosis_report(
    run: WorkflowRun,
    payload: DiagnosisReportPayload,
    *,
    version: int = 1,
    confidence: Decimal | None = None,
) -> DiagnosisReport:
    return DiagnosisReport(
        workflow_run_id=run.id,
        version=version,
        summary=payload.summary,
        root_causes=[item.model_dump(mode="json") for item in payload.root_causes],
        evidence=[item.model_dump(mode="json") for item in payload.evidence],
        recommendations=[item.model_dump(mode="json") for item in payload.recommendations],
        confidence=confidence or Decimal(str(payload.confidence)),
        model_name=payload.model_name,
        prompt_version=payload.prompt_version,
    )


def test_workflow_state_is_strict_and_json_serializable() -> None:
    state = AlertWorkflowState(
        alert_id=uuid4(),
        workflow_run_id=uuid4(),
        thread_id="thread-state-001",
        alert={"service": "order-service", "value": 92.5},
    )

    serialized = state.model_dump(mode="json")

    assert serialized["schema_version"] == "1.0"
    assert serialized["report_version"] == 0
    assert isinstance(serialized["alert_id"], str)
    with pytest.raises(ValidationError):
        AlertWorkflowState.model_validate({**serialized, "unexpected": True})


def test_report_and_human_decision_contracts_reject_untrusted_output() -> None:
    valid_report = report_payload()
    invalid_report = valid_report.model_dump()
    invalid_report["root_causes"][0]["evidence_refs"] = ["missing-evidence"]

    with pytest.raises(ValidationError, match="unknown evidence references"):
        DiagnosisReportPayload.model_validate(invalid_report)
    with pytest.raises(ValidationError, match="comment is required"):
        HumanDecisionCommand(
            idempotency_key="decision-reanalyze-001",
            action=HumanDecisionAction.REANALYZE,
            comment="  ",
        )


def test_status_transition_guards_allow_only_documented_paths() -> None:
    ensure_alert_status_transition(AlertStatus.RECEIVED, AlertStatus.RUNNING)
    ensure_alert_status_transition(AlertStatus.RUNNING, AlertStatus.WAITING_FOR_APPROVAL)
    ensure_alert_status_transition(AlertStatus.WAITING_FOR_APPROVAL, AlertStatus.REANALYZING)
    ensure_alert_status_transition(AlertStatus.REANALYZING, AlertStatus.WAITING_FOR_APPROVAL)
    ensure_alert_status_transition(AlertStatus.WAITING_FOR_APPROVAL, AlertStatus.COMPLETED)
    ensure_alert_status_transition(AlertStatus.COMPLETED, AlertStatus.COMPLETED)
    ensure_workflow_run_status_transition(WorkflowRunStatus.FAILED, WorkflowRunStatus.QUEUED)

    with pytest.raises(InvalidStatusTransition):
        ensure_alert_status_transition(AlertStatus.RECEIVED, AlertStatus.COMPLETED)
    with pytest.raises(InvalidStatusTransition):
        ensure_workflow_run_status_transition(
            WorkflowRunStatus.COMPLETED, WorkflowRunStatus.RUNNING
        )


@pytest.mark.asyncio
async def test_persists_complete_workflow_audit_chain(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = report_payload()
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        event = WorkflowEvent(
            workflow_run_id=run.id,
            sequence=1,
            idempotency_key="event-workflow-queued-001",
            event_type=WorkflowEventType.WORKFLOW_QUEUED,
            status=WorkflowRunStatus.QUEUED,
            payload={"workflow_version": "1.0"},
        )
        tool = ToolExecution(
            workflow_run_id=run.id,
            node_name="collect_context",
            tool_name="metrics",
            idempotency_key="tool-metrics-001",
            status=ToolExecutionStatus.SUCCEEDED,
            input_payload={"service": "order-service"},
            output_payload={"cpu": 92.5},
            duration_ms=120,
        )
        report = diagnosis_report(run, payload)
        session.add_all([event, tool, report])
        await session.flush()
        decision = HumanDecision(
            diagnosis_report_id=report.id,
            idempotency_key="decision-approve-001",
            action=HumanDecisionAction.APPROVE,
            comment="Confirmed by on-call engineer.",
            actor="demo-user",
        )
        session.add(decision)
        await session.commit()

        persisted_run = await session.scalar(select(WorkflowRun).where(WorkflowRun.id == run.id))
        persisted_report = await session.scalar(
            select(DiagnosisReport).where(DiagnosisReport.id == report.id)
        )

        assert persisted_run is not None
        assert persisted_run.idempotency_key == "start-workflow-1"
        assert persisted_report is not None
        assert persisted_report.confidence == Decimal("0.9100")
        assert decision.diagnosis_report_id == persisted_report.id


@pytest.mark.asyncio
async def test_only_one_active_run_per_alert(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with test_session_factory() as session:
        alert = await create_alert(session)
        session.add_all([workflow_run(alert, "one"), workflow_run(alert, "two")])

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_terminal_run_allows_a_new_active_run(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with test_session_factory() as session:
        alert = await create_alert(session)
        session.add(
            workflow_run(
                alert,
                "completed",
                status=WorkflowRunStatus.COMPLETED,
                started_at=now,
                finished_at=now,
            )
        )
        await session.flush()
        session.add(workflow_run(alert, "retry"))
        await session.commit()

        runs = list(
            (
                await session.scalars(select(WorkflowRun).where(WorkflowRun.alert_id == alert.id))
            ).all()
        )
        assert len(runs) == 2


@pytest.mark.asyncio
async def test_event_sequence_and_idempotency_are_database_enforced(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        session.add_all(
            [
                WorkflowEvent(
                    workflow_run_id=run.id,
                    sequence=1,
                    idempotency_key="event-one-001",
                    event_type=WorkflowEventType.WORKFLOW_QUEUED,
                    payload={},
                ),
                WorkflowEvent(
                    workflow_run_id=run.id,
                    sequence=1,
                    idempotency_key="event-two-002",
                    event_type=WorkflowEventType.WORKFLOW_STARTED,
                    payload={},
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_tool_execution_idempotency_is_database_enforced(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        common = {
            "workflow_run_id": run.id,
            "node_name": "collect_context",
            "tool_name": "metrics",
            "idempotency_key": "tool-metrics-same-key",
            "input_payload": {"service": "order-service"},
        }
        session.add_all([ToolExecution(**common), ToolExecution(**common)])

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_workflow_start_idempotency_is_global(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with test_session_factory() as session:
        first_alert = await create_alert(session, "first-workflow-alert")
        second_alert = await create_alert(session, "second-workflow-alert")
        session.add_all(
            [
                workflow_run(first_alert, "first"),
                WorkflowRun(
                    alert_id=second_alert.id,
                    thread_id="thread-second",
                    idempotency_key="start-workflow-first",
                    workflow_version="1.0",
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_event_idempotency_is_database_enforced(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        session.add_all(
            [
                WorkflowEvent(
                    workflow_run_id=run.id,
                    sequence=1,
                    idempotency_key="same-event-key",
                    event_type=WorkflowEventType.WORKFLOW_QUEUED,
                    payload={},
                ),
                WorkflowEvent(
                    workflow_run_id=run.id,
                    sequence=2,
                    idempotency_key="same-event-key",
                    event_type=WorkflowEventType.WORKFLOW_STARTED,
                    payload={},
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_report_versions_are_unique_within_a_run(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = report_payload()
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        session.add_all([diagnosis_report(run, payload), diagnosis_report(run, payload)])

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_report_confidence_is_database_enforced(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = report_payload()
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        session.add(diagnosis_report(run, payload, confidence=Decimal("1.1")))

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_database_requires_feedback_for_reanalysis(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = report_payload()
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        report = diagnosis_report(run, payload)
        session.add(report)
        await session.flush()
        session.add(
            HumanDecision(
                diagnosis_report_id=report.id,
                idempotency_key="decision-reanalyze-empty",
                action=HumanDecisionAction.REANALYZE,
                comment="  ",
                actor="demo-user",
            )
        )

        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_each_report_accepts_only_one_human_decision(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = report_payload()
    async with test_session_factory() as session:
        alert = await create_alert(session)
        run = workflow_run(alert)
        session.add(run)
        await session.flush()
        report = diagnosis_report(run, payload)
        session.add(report)
        await session.flush()
        session.add_all(
            [
                HumanDecision(
                    diagnosis_report_id=report.id,
                    idempotency_key="decision-approve-first",
                    action=HumanDecisionAction.APPROVE,
                    actor="demo-user",
                ),
                HumanDecision(
                    diagnosis_report_id=report.id,
                    idempotency_key="decision-reject-second",
                    action=HumanDecisionAction.REJECT,
                    actor="demo-user",
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()
