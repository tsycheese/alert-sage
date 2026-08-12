from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.feishu.client import FeishuApiError
from app.integrations.feishu.coordination import (
    schedule_approval_reminders,
    schedule_card_sync,
    schedule_private_reminder_resolutions,
)
from app.integrations.feishu.delivery import FeishuDeliveryService
from app.models.alert import Alert
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.enums import (
    AlertStatus,
    FeishuCardStatus,
    FeishuDeliveryKind,
    FeishuDeliveryStatus,
    HumanActorSource,
    HumanDecisionAction,
    WorkflowRunStatus,
)
from app.models.feishu import FeishuCardBinding, FeishuDelivery
from app.models.workflow import WorkflowRun
from app.schemas.alert import AlertCreate
from app.services.alerts import AlertService

DATASET_ID = "8dc8a66d-8202-4099-b0ee-6d42e0bf57d1"


def settings(role: str) -> Settings:
    common = {
        "_env_file": None,
        "runtime_profile": "real",
        "component_role": role,
        "knowledge_provider": "dify",
        "dify_api_key": "dify-secret",
        "dify_dataset_id": DATASET_ID,
        "diagnostic_model_provider": "deepseek",
        "feishu_enabled": True,
        "feishu_app_id": "cli_test",
        "feishu_target_chat_id": "oc_test",
        "feishu_approvers": {"ou_approver": "Primary on-call"},
        "feishu_web_base_url": "https://alerts.example.test",
    }
    if role == "api":
        common.update(
            {
                "feishu_verification_token": "verification-token",
                "feishu_encrypt_key": "encrypt-key",
                "feishu_expected_tenant_key": "tenant-test",
                "feishu_source_allowlist": ["synthetic-monitor"],
            }
        )
    else:
        common.update(
            {
                "diagnostic_model_api_key": "deepseek-secret",
                "feishu_app_secret": "app-secret",
            }
        )
    return Settings(**common)


class RecordingClient:
    def __init__(
        self,
        *,
        error: FeishuApiError | None = None,
        update_error: FeishuApiError | None = None,
    ) -> None:
        self.error = error
        self.update_error = update_error
        self.sent = 0
        self.updated = 0
        self.sent_cards: list[dict[str, object]] = []
        self.updated_cards: list[dict[str, object]] = []

    async def send_card(self, **kwargs: object) -> str:
        self.sent += 1
        self.sent_cards.append(kwargs["card"])  # type: ignore[arg-type]
        if self.error:
            raise self.error
        return f"om_{self.sent}"

    async def update_card(self, **kwargs: object) -> None:
        self.updated += 1
        self.updated_cards.append(kwargs["card"])  # type: ignore[arg-type]
        if self.update_error:
            raise self.update_error
        if self.error:
            raise self.error


async def create_deliveries(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Alert, list[FeishuDelivery]]:
    api_settings = settings("api")
    async with session_factory() as session:
        created = await AlertService(session, settings=api_settings).create(
            AlertCreate(
                source="synthetic-monitor",
                external_alert_id="delivery-order-alert",
                alert_name="SyntheticHighCPU",
                service="synthetic-service",
                severity="critical",
                value=92,
                threshold=80,
                started_at=datetime.now(UTC),
            )
        )
        first = await session.scalar(
            select(FeishuDelivery)
            .join(FeishuCardBinding)
            .where(FeishuCardBinding.alert_id == created.alert.id)
        )
        assert first is not None
        second = await schedule_card_sync(
            session,
            alert_id=created.alert.id,
            reason="running",
            settings=api_settings,
        )
        third = await schedule_card_sync(
            session,
            alert_id=created.alert.id,
            reason="waiting",
            settings=api_settings,
        )
        assert second is not None and third is not None
        await session.commit()
        return created.alert, [first, second, third]


async def create_private_resolution(
    session_factory: async_sessionmaker[AsyncSession],
    client: RecordingClient,
) -> tuple[Alert, FeishuDelivery, FeishuDelivery]:
    api_settings = settings("api")
    async with session_factory() as session:
        created = await AlertService(session, settings=api_settings).create(
            AlertCreate(
                source="synthetic-monitor",
                external_alert_id=f"private-resolution-{uuid4()}",
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
            idempotency_key=f"private-resolution-run-{uuid4()}",
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
        waiting_delivery = await schedule_card_sync(
            session,
            alert_id=alert.id,
            reason="waiting",
            settings=api_settings,
        )
        assert waiting_delivery is not None
        reminders = await schedule_approval_reminders(
            session,
            alert_id=alert.id,
            settings=api_settings,
        )
        assert len(reminders) == 1
        reminder_id = reminders[0].id
        report_id = report.id
        await session.commit()

    service = FeishuDeliveryService(
        session_factory=session_factory,
        client=client,
        settings=settings("worker"),
    )
    assert (await service.execute(reminder_id)).skipped_stale is False

    async with session_factory() as session:
        reminder = await session.get(FeishuDelivery, reminder_id)
        assert reminder is not None and reminder.message_id == "om_1"
        decision = HumanDecision(
            diagnosis_report_id=report_id,
            idempotency_key=f"private-resolution-decision-{uuid4()}",
            action=HumanDecisionAction.REJECT,
            comment=None,
            actor="Primary on-call",
            actor_source=HumanActorSource.FEISHU,
            actor_subject="ou_approver",
            actor_display_name="Primary on-call",
        )
        session.add(decision)
        await session.flush()
        resolutions = await schedule_private_reminder_resolutions(
            session,
            alert_id=alert.id,
            decision=decision,
            settings=api_settings,
        )
        assert len(resolutions) == 1
        resolution_id = resolutions[0].id
        decision_delivery = await schedule_card_sync(
            session,
            alert_id=alert.id,
            reason=f"decision-{decision.id}",
            settings=api_settings,
        )
        assert decision_delivery is not None
        await session.commit()

    async with session_factory() as session:
        stored_reminder = await session.get(FeishuDelivery, reminder_id)
        resolution = await session.get(FeishuDelivery, resolution_id)
        assert stored_reminder is not None and resolution is not None
        return alert, stored_reminder, resolution


@pytest.mark.asyncio
async def test_old_revision_never_overwrites_latest_card(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert, deliveries = await create_deliveries(test_session_factory)
    client = RecordingClient()
    service = FeishuDeliveryService(
        session_factory=test_session_factory,
        client=client,
        settings=settings("worker"),
    )
    old = await service.execute(deliveries[0].id)
    newest = await service.execute(deliveries[-1].id)
    assert old.skipped_stale is True
    assert newest.skipped_stale is False
    assert client.sent == 1
    async with test_session_factory() as session:
        binding = await session.scalar(
            select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert.id)
        )
        assert binding is not None
        assert binding.delivered_revision == 3
        assert binding.status == FeishuCardStatus.ACTIVE


@pytest.mark.asyncio
async def test_feishu_failure_changes_only_channel_state(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert, deliveries = await create_deliveries(test_session_factory)
    service = FeishuDeliveryService(
        session_factory=test_session_factory,
        client=RecordingClient(error=FeishuApiError("feishu_unavailable")),
        settings=settings("worker"),
    )
    with pytest.raises(FeishuApiError, match="feishu_unavailable"):
        await service.execute(deliveries[-1].id)
    async with test_session_factory() as session:
        stored_alert = await session.get(Alert, alert.id)
        binding = await session.scalar(
            select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert.id)
        )
        delivery = await session.get(FeishuDelivery, deliveries[-1].id)
        assert stored_alert is not None and stored_alert.status == AlertStatus.RECEIVED
        assert binding is not None and binding.status == FeishuCardStatus.FAILED
        assert binding.last_error_code == "feishu_unavailable"
        assert delivery is not None and delivery.status == FeishuDeliveryStatus.FAILED


@pytest.mark.asyncio
async def test_private_reminder_resolution_updates_old_revision_without_actions(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = RecordingClient()
    alert, reminder, resolution = await create_private_resolution(
        test_session_factory,
        client,
    )
    service = FeishuDeliveryService(
        session_factory=test_session_factory,
        client=client,
        settings=settings("worker"),
    )

    result = await service.execute(resolution.id)

    assert result.skipped_stale is False
    assert client.sent == 1
    assert client.updated == 1
    assert client.updated_cards[0]["header"]["title"]["content"].startswith("已驳回：")
    async with test_session_factory() as session:
        stored = await session.get(FeishuDelivery, resolution.id)
        binding = await session.scalar(
            select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert.id)
        )
        assert stored is not None
        assert stored.status == FeishuDeliveryStatus.SUCCEEDED
        assert stored.message_id == reminder.message_id
        assert stored.decision_id is not None
        assert binding is not None and binding.desired_revision > resolution.revision


@pytest.mark.asyncio
async def test_private_reminder_resolution_replaces_expired_message(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = RecordingClient(update_error=FeishuApiError("message_expired", replace_message=True))
    _, _, resolution = await create_private_resolution(test_session_factory, client)
    service = FeishuDeliveryService(
        session_factory=test_session_factory,
        client=client,
        settings=settings("worker"),
    )

    await service.execute(resolution.id)

    assert client.updated == 1
    assert client.sent == 2
    async with test_session_factory() as session:
        stored = await session.get(FeishuDelivery, resolution.id)
        assert stored is not None and stored.message_id == "om_2"


@pytest.mark.asyncio
async def test_private_reminder_failure_does_not_fail_shared_binding(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = RecordingClient(update_error=FeishuApiError("private_update_failed"))
    alert, _, resolution = await create_private_resolution(test_session_factory, client)
    service = FeishuDeliveryService(
        session_factory=test_session_factory,
        client=client,
        settings=settings("worker"),
    )

    with pytest.raises(FeishuApiError, match="private_update_failed"):
        await service.execute(resolution.id)

    async with test_session_factory() as session:
        stored = await session.get(FeishuDelivery, resolution.id)
        binding = await session.scalar(
            select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert.id)
        )
        assert stored is not None and stored.status == FeishuDeliveryStatus.FAILED
        assert stored.kind == FeishuDeliveryKind.PRIVATE_REMINDER
        assert binding is not None and binding.status != FeishuCardStatus.FAILED
        assert binding.last_error_code is None
