import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select

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
from app.schemas.workflow import HumanDecisionCommand
from app.workflows.alert.adapters import (
    ContextProviderResult,
    MockCmdbProvider,
    MockDiagnosticModel,
    MockKnowledgeProvider,
    MockMetricsProvider,
)
from app.workflows.alert.checkpoint import open_alert_workflow_service
from app.workflows.alert.service import WorkflowExecutionError


async def create_runtime_alert(session_factory: Any, suffix: str) -> Alert:
    async with session_factory() as session:
        alert = Alert(
            source="runtime-test",
            external_alert_id=f"runtime-alert-{suffix}",
            fingerprint=f"runtime-fingerprint-{suffix}",
            alert_name="HighCPUUsage",
            service="order-service",
            instance="order-service-01",
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.RECEIVED,
            payload={"value": 92.5, "threshold": 80},
            started_at=datetime(2026, 7, 19, 2, 0, tzinfo=UTC),
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        return alert


def decision(
    action: HumanDecisionAction,
    suffix: str,
    *,
    comment: str | None = None,
) -> HumanDecisionCommand:
    return HumanDecisionCommand(
        idempotency_key=f"runtime-decision-{suffix}",
        action=action,
        comment=comment,
    )


def run_workflow_process(database_url: str, *arguments: str) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["ALERT_SAGE_DATABASE_URL"] = database_url
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.run_workflow", *arguments],
        cwd=Path(__file__).parents[1],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.asyncio
async def test_workflow_runs_seven_nodes_and_pauses_for_human_review(
    isolated_test_database: Any,
) -> None:
    alert = await create_runtime_alert(isolated_test_database.session_factory, "pause")

    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
    ) as service:
        result = await service.start(
            alert_id=alert.id,
            idempotency_key="runtime-start-pause",
        )
        replay = await service.start(
            alert_id=alert.id,
            idempotency_key="runtime-start-pause",
        )

    assert result.status == WorkflowRunStatus.WAITING_FOR_APPROVAL
    assert replay.workflow_run_id == result.workflow_run_id
    assert result.current_node == "human_review"
    assert result.state.report_version == 1
    assert result.state.report_id is not None
    assert set(result.state.contexts) == {"metrics", "logs", "cmdb", "knowledge"}

    async with isolated_test_database.session_factory() as session:
        node_events = list(
            (
                await session.scalars(
                    select(WorkflowEvent)
                    .where(
                        WorkflowEvent.workflow_run_id == result.workflow_run_id,
                        WorkflowEvent.event_type == WorkflowEventType.NODE_COMPLETED,
                    )
                    .order_by(WorkflowEvent.sequence)
                )
            ).all()
        )
        tool_count = await session.scalar(
            select(func.count())
            .select_from(ToolExecution)
            .where(ToolExecution.workflow_run_id == result.workflow_run_id)
        )
        report_count = await session.scalar(
            select(func.count())
            .select_from(DiagnosisReport)
            .where(DiagnosisReport.workflow_run_id == result.workflow_run_id)
        )
        queued_event_count = await session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == result.workflow_run_id,
                WorkflowEvent.event_type == WorkflowEventType.WORKFLOW_QUEUED,
            )
        )

    assert [event.node_name for event in node_events] == [
        "parse_alert",
        "classify_alert",
        "collect_context",
        "diagnose",
        "recommend",
    ]
    assert tool_count == 4
    assert report_count == 1
    assert queued_event_count == 1


@pytest.mark.asyncio
async def test_postgres_checkpoint_resumes_after_runtime_restart(
    isolated_test_database: Any,
) -> None:
    alert = await create_runtime_alert(isolated_test_database.session_factory, "restart")
    interrupted = run_workflow_process(
        isolated_test_database.checkpoint_url,
        "start",
        "--alert-id",
        str(alert.id),
        "--idempotency-key",
        "runtime-start-restart",
    )
    completed = run_workflow_process(
        isolated_test_database.checkpoint_url,
        "resume",
        "--workflow-run-id",
        interrupted["workflow_run_id"],
        "--action",
        "approve",
        "--decision-key",
        "runtime-decision-restart",
        "--actor",
        "on-call@example.com",
    )

    assert completed["thread_id"] == interrupted["thread_id"]
    assert completed["status"] == WorkflowRunStatus.COMPLETED
    assert completed["final_status"] == "completed"
    assert completed["report_version"] == 1
    completed_run_id = UUID(completed["workflow_run_id"])

    async with isolated_test_database.session_factory() as session:
        persisted_alert = await session.get(Alert, alert.id)
        persisted_run = await session.get(WorkflowRun, completed_run_id)
        decisions = list((await session.scalars(select(HumanDecision))).all())
        completed_events = await session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == completed_run_id,
                WorkflowEvent.event_type == WorkflowEventType.WORKFLOW_COMPLETED,
            )
        )
        node_events = list(
            (
                await session.scalars(
                    select(WorkflowEvent)
                    .where(
                        WorkflowEvent.workflow_run_id == completed_run_id,
                        WorkflowEvent.event_type == WorkflowEventType.NODE_COMPLETED,
                    )
                    .order_by(WorkflowEvent.sequence)
                )
            ).all()
        )

    assert persisted_alert is not None
    assert persisted_alert.status == AlertStatus.COMPLETED
    assert persisted_run is not None
    assert persisted_run.finished_at is not None
    assert len(decisions) == 1
    assert completed_events == 1
    assert [event.node_name for event in node_events] == [
        "parse_alert",
        "classify_alert",
        "collect_context",
        "diagnose",
        "recommend",
        "human_review",
        "finalize",
    ]


@pytest.mark.asyncio
async def test_reanalysis_creates_a_new_report_without_duplicate_decisions(
    isolated_test_database: Any,
) -> None:
    alert = await create_runtime_alert(isolated_test_database.session_factory, "reanalyze")
    reanalyze = decision(
        HumanDecisionAction.REANALYZE,
        "reanalyze",
        comment="Check whether the latest release changed the query plan.",
    )
    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
    ) as service:
        first = await service.start(
            alert_id=alert.id,
            idempotency_key="runtime-start-reanalyze",
        )
        second = await service.resume(
            workflow_run_id=first.workflow_run_id,
            command=reanalyze,
            actor="on-call@example.com",
        )
        replay = await service.resume(
            workflow_run_id=first.workflow_run_id,
            command=reanalyze,
            actor="on-call@example.com",
        )
        completed = await service.resume(
            workflow_run_id=first.workflow_run_id,
            command=decision(HumanDecisionAction.APPROVE, "reanalyze-approve"),
            actor="on-call@example.com",
        )

    assert second.status == WorkflowRunStatus.WAITING_FOR_APPROVAL
    assert second.state.report_version == 2
    assert second.state.reanalysis_count == 1
    assert replay.state.report_version == 2
    assert completed.status == WorkflowRunStatus.COMPLETED

    async with isolated_test_database.session_factory() as session:
        reports = list(
            (
                await session.scalars(
                    select(DiagnosisReport)
                    .where(DiagnosisReport.workflow_run_id == first.workflow_run_id)
                    .order_by(DiagnosisReport.version)
                )
            ).all()
        )
        decisions = list((await session.scalars(select(HumanDecision))).all())

    assert [report.version for report in reports] == [1, 2]
    assert "operator feedback" in reports[1].summary
    assert len(decisions) == 2


@pytest.mark.asyncio
async def test_reject_decision_moves_workflow_and_alert_to_rejected(
    isolated_test_database: Any,
) -> None:
    alert = await create_runtime_alert(isolated_test_database.session_factory, "reject")
    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
    ) as service:
        interrupted = await service.start(
            alert_id=alert.id,
            idempotency_key="runtime-start-reject",
        )
        rejected = await service.resume(
            workflow_run_id=interrupted.workflow_run_id,
            command=decision(
                HumanDecisionAction.REJECT,
                "reject",
                comment="Evidence is insufficient for the proposed action.",
            ),
            actor="on-call@example.com",
        )

    assert rejected.status == WorkflowRunStatus.REJECTED
    assert rejected.state.final_status == "rejected"
    async with isolated_test_database.session_factory() as session:
        persisted_alert = await session.get(Alert, alert.id)
        rejected_events = await session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == rejected.workflow_run_id,
                WorkflowEvent.event_type == WorkflowEventType.WORKFLOW_REJECTED,
            )
        )

    assert persisted_alert is not None
    assert persisted_alert.status == AlertStatus.REJECTED
    assert rejected_events == 1


class FailingLogsProvider:
    name = "logs"

    async def collect(self, alert: Any) -> ContextProviderResult:
        del alert
        raise TimeoutError("mock logs timeout")


@pytest.mark.asyncio
async def test_partial_tool_failure_is_persisted_and_diagnosis_continues(
    isolated_test_database: Any,
) -> None:
    alert = await create_runtime_alert(isolated_test_database.session_factory, "partial")
    providers = (
        MockMetricsProvider(),
        FailingLogsProvider(),
        MockCmdbProvider(),
        MockKnowledgeProvider(),
    )
    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
        context_providers=providers,
        tool_max_attempts=2,
    ) as service:
        result = await service.start(
            alert_id=alert.id,
            idempotency_key="runtime-start-partial",
        )

    assert result.status == WorkflowRunStatus.WAITING_FOR_APPROVAL
    assert set(result.state.contexts) == {"metrics", "cmdb", "knowledge"}
    assert len(result.state.tool_errors) == 1
    assert result.state.tool_errors[0].attempts == 2
    assert result.state.warnings

    async with isolated_test_database.session_factory() as session:
        executions = list(
            (
                await session.scalars(
                    select(ToolExecution)
                    .where(ToolExecution.workflow_run_id == result.workflow_run_id)
                    .order_by(ToolExecution.tool_name)
                )
            ).all()
        )

    assert len(executions) == 4
    failed = next(item for item in executions if item.tool_name == "logs")
    assert failed.status == ToolExecutionStatus.FAILED
    assert failed.attempt == 2
    assert failed.error_code == "tool_timeout"


class InvalidDiagnosticModel(MockDiagnosticModel):
    async def diagnose(self, **kwargs: Any) -> object:
        del kwargs
        return {"summary": "unvalidated free text"}


@pytest.mark.asyncio
async def test_invalid_model_output_fails_without_persisting_a_report(
    isolated_test_database: Any,
) -> None:
    alert = await create_runtime_alert(isolated_test_database.session_factory, "invalid-model")
    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
        diagnostic_model=InvalidDiagnosticModel(),
    ) as service:
        with pytest.raises(WorkflowExecutionError) as captured:
            await service.start(
                alert_id=alert.id,
                idempotency_key="runtime-start-invalid-model",
            )

    async with isolated_test_database.session_factory() as session:
        run = await session.get(WorkflowRun, captured.value.workflow_run_id)
        persisted_alert = await session.get(Alert, alert.id)
        report_count = await session.scalar(
            select(func.count())
            .select_from(DiagnosisReport)
            .where(DiagnosisReport.workflow_run_id == captured.value.workflow_run_id)
        )
        failure_count = await session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(
                WorkflowEvent.workflow_run_id == captured.value.workflow_run_id,
                WorkflowEvent.event_type == WorkflowEventType.WORKFLOW_FAILED,
            )
        )

    assert run is not None
    assert run.status == WorkflowRunStatus.FAILED
    assert persisted_alert is not None
    assert persisted_alert.status == AlertStatus.FAILED
    assert report_count == 0
    assert failure_count == 1
