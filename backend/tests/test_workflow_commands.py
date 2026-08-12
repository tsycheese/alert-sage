import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.alert import Alert
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.enums import (
    AlertStatus,
    FeishuDeliveryKind,
    FeishuDeliveryStatus,
    HumanActorSource,
    HumanDecisionAction,
    OutboxTopic,
    WorkflowRunStatus,
)
from app.models.feishu import FeishuCardBinding, FeishuDelivery
from app.models.outbox import OutboxMessage
from app.models.workflow import WorkflowRun
from app.schemas.alert import AlertCreate
from app.schemas.workflow import WorkflowResumePayload
from app.services.alerts import AlertService
from app.services.workflow_commands import (
    WorkflowCommandService,
    WorkflowCommandStateConflictError,
)


def offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_profile="test",
        component_role="test",
        knowledge_provider="mock",
        diagnostic_model_provider="mock",
    )


def feishu_settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_profile="real",
        component_role="api",
        knowledge_provider="dify",
        dify_api_key="dify-secret",
        dify_dataset_id="8dc8a66d-8202-4099-b0ee-6d42e0bf57d1",
        diagnostic_model_provider="deepseek",
        feishu_enabled=True,
        feishu_app_id="cli_test",
        feishu_verification_token="verification-token",
        feishu_encrypt_key="encrypt-key",
        feishu_expected_tenant_key="tenant-test",
        feishu_target_chat_id="oc_test",
        feishu_source_allowlist=["synthetic-monitor"],
        feishu_approvers={"ou_approver": "Primary on-call"},
        feishu_web_base_url="https://alerts.example.test",
    )


async def create_alert(session_factory: async_sessionmaker[AsyncSession], suffix: str) -> Alert:
    async with session_factory() as session:
        return (
            await AlertService(session, settings=offline_settings()).create(
                AlertCreate(
                    source="web",
                    external_alert_id=f"command-{suffix}",
                    alert_name="SyntheticHighCPU",
                    service="synthetic-service",
                    severity="critical",
                    value=92,
                    threshold=80,
                    started_at=datetime.now(UTC),
                )
            )
        ).alert


@pytest.mark.asyncio
async def test_web_and_feishu_start_race_returns_one_active_run(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = await create_alert(test_session_factory, "start-race")

    async def prepare(key: str) -> tuple[bool, object]:
        async with test_session_factory() as session:
            result = await WorkflowCommandService(
                session, settings=offline_settings()
            ).prepare_start(alert_id=alert.id, idempotency_key=key)
            await session.commit()
            return result.created, result.run.id

    results = await asyncio.gather(prepare("web-start-race"), prepare("feishu-start-race"))
    assert sorted(created for created, _ in results) == [False, True]
    assert len({run_id for _, run_id in results}) == 1
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 1


@pytest.mark.asyncio
async def test_fourth_reanalysis_is_rejected_from_database_facts(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert = await create_alert(test_session_factory, "reanalysis-limit")
    async with test_session_factory() as session:
        stored_alert = await session.get(Alert, alert.id)
        assert stored_alert is not None
        stored_alert.status = AlertStatus.WAITING_FOR_APPROVAL
        run = WorkflowRun(
            id=uuid4(),
            alert_id=alert.id,
            thread_id=str(uuid4()),
            idempotency_key="reanalysis-limit-run",
            workflow_version="1.0",
            status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
            current_node="human_review",
        )
        session.add(run)
        await session.flush()
        for version in range(1, 5):
            report = DiagnosisReport(
                workflow_run_id=run.id,
                version=version,
                summary=f"Synthetic diagnosis {version}",
                root_causes=[{"title": "Synthetic cause"}],
                evidence=[{"type": "metric", "source": "synthetic://metric"}],
                recommendations=[{"title": "Inspect synthetic load"}],
                confidence=Decimal("0.8000"),
                model_name="synthetic-model",
                prompt_version="test-v1",
            )
            session.add(report)
            await session.flush()
            if version < 4:
                session.add(
                    HumanDecision(
                        diagnosis_report_id=report.id,
                        idempotency_key=f"reanalysis-{version}",
                        action=HumanDecisionAction.REANALYZE,
                        comment=f"Synthetic feedback {version}",
                        actor="Web test actor",
                        actor_source=HumanActorSource.WEB,
                    )
                )
        await session.commit()

    async with test_session_factory() as session:
        with pytest.raises(WorkflowCommandStateConflictError, match="maximum of 3"):
            await WorkflowCommandService(session, settings=offline_settings()).prepare_decision(
                workflow_run_id=run.id,
                resume=WorkflowResumePayload(
                    idempotency_key="reanalysis-4",
                    action=HumanDecisionAction.REANALYZE,
                    comment="Fourth synthetic feedback",
                    actor="Primary on-call",
                    actor_source=HumanActorSource.FEISHU,
                    actor_subject="ou_approver",
                    actor_display_name="Primary on-call",
                ),
            )


@pytest.mark.asyncio
async def test_feishu_decision_schedules_private_resolution_in_same_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    configured = feishu_settings()
    async with test_session_factory() as session:
        created = await AlertService(session, settings=configured).create(
            AlertCreate(
                source="synthetic-monitor",
                external_alert_id="private-resolution-command",
                alert_name="SyntheticHighCPU",
                service="synthetic-service",
                severity="critical",
                value=92,
                threshold=80,
                started_at=datetime.now(UTC),
            )
        )
        alert = created.alert
        alert.status = AlertStatus.WAITING_FOR_APPROVAL
        run = WorkflowRun(
            alert_id=alert.id,
            thread_id=str(uuid4()),
            idempotency_key="private-resolution-command-run",
            workflow_version="1.0",
            status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
            current_node="human_review",
        )
        session.add(run)
        await session.flush()
        report = DiagnosisReport(
            workflow_run_id=run.id,
            version=1,
            summary="Synthetic diagnosis",
            root_causes=[{"title": "Synthetic cause"}],
            evidence=[{"type": "metric", "source": "synthetic://metric"}],
            recommendations=[{"title": "Inspect synthetic load"}],
            confidence=Decimal("0.8000"),
            model_name="synthetic-model",
            prompt_version="test-v1",
        )
        session.add(report)
        await session.flush()
        binding = await session.scalar(
            select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert.id)
        )
        assert binding is not None
        reminder = FeishuDelivery(
            binding_id=binding.id,
            kind=FeishuDeliveryKind.PRIVATE_REMINDER,
            recipient_open_id="ou_approver",
            revision=binding.desired_revision,
            action_nonce="waiting-nonce",
            idempotency_key="private-resolution-command-reminder",
            status=FeishuDeliveryStatus.SUCCEEDED,
            attempts=1,
            message_id="om_private",
        )
        session.add(reminder)
        await session.flush()

        prepared = await WorkflowCommandService(
            session,
            settings=configured,
        ).prepare_decision(
            workflow_run_id=run.id,
            resume=WorkflowResumePayload(
                idempotency_key="private-resolution-command-decision",
                action=HumanDecisionAction.REJECT,
                comment=None,
                actor="Primary on-call",
                actor_source=HumanActorSource.FEISHU,
                actor_subject="ou_approver",
                actor_display_name="Primary on-call",
            ),
        )
        resolution = await session.scalar(
            select(FeishuDelivery).where(FeishuDelivery.decision_id == prepared.decision_id)
        )
        assert resolution is not None
        outbox = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.aggregate_id == resolution.id)
        )
        assert outbox is not None
        assert outbox.topic == OutboxTopic.FEISHU_REMINDER_SEND
        assert resolution.message_id == "om_private"
        assert resolution.recipient_open_id == "ou_approver"
        assert resolution.status == FeishuDeliveryStatus.PENDING
        assert binding.desired_revision == resolution.revision + 1
        await session.commit()
