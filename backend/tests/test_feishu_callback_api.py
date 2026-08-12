import base64
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.main import app
from app.models.alert import Alert
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.enums import (
    AlertStatus,
    FeishuCallbackStatus,
    FeishuDeliveryKind,
    FeishuDeliveryStatus,
    OutboxTopic,
    WorkflowRunStatus,
)
from app.models.feishu import FeishuCallbackEvent, FeishuCardBinding, FeishuDelivery
from app.models.outbox import OutboxMessage
from app.models.workflow import WorkflowRun
from app.schemas.alert import AlertCreate
from app.services.alerts import AlertService

DATASET_ID = "8dc8a66d-8202-4099-b0ee-6d42e0bf57d1"
ENCRYPT_KEY = "synthetic-encrypt-key"
VERIFICATION_TOKEN = "synthetic-verification-token"


def api_settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_profile="real",
        component_role="api",
        knowledge_provider="dify",
        dify_api_key="dify-secret",
        dify_dataset_id=DATASET_ID,
        diagnostic_model_provider="deepseek",
        feishu_enabled=True,
        feishu_app_id="cli_test",
        feishu_verification_token=VERIFICATION_TOKEN,
        feishu_encrypt_key=ENCRYPT_KEY,
        feishu_expected_tenant_key="tenant-test",
        feishu_target_chat_id="oc_test",
        feishu_source_allowlist=["synthetic-monitor"],
        feishu_approvers={"ou_approver": "Primary on-call"},
        feishu_web_base_url="https://alerts.example.test",
    )


def encrypt(payload: dict[str, object]) -> bytes:
    key = hashlib.sha256(ENCRYPT_KEY.encode()).digest()
    iv = bytes(range(16))
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    ciphertext = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(pad(plaintext, AES.block_size))
    return json.dumps(
        {"encrypt": base64.b64encode(iv + ciphertext).decode()},
        separators=(",", ":"),
    ).encode()


def signed_headers(body: bytes, timestamp: int | str) -> dict[str, str]:
    nonce = "synthetic-nonce"
    timestamp_value = str(timestamp)
    signed = timestamp_value.encode() + nonce.encode() + ENCRYPT_KEY.encode() + body
    return {
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": timestamp_value,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": hashlib.sha256(signed).hexdigest(),
    }


async def create_binding(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> tuple[FeishuCardBinding, FeishuDelivery]:
    async with session_factory() as session:
        result = await AlertService(session, settings=settings).create(
            AlertCreate(
                source="synthetic-monitor",
                external_alert_id="feishu-callback-alert",
                alert_name="SyntheticHighCPU",
                service="synthetic-service",
                instance="synthetic-01",
                severity="critical",
                value=92,
                threshold=80,
                started_at=datetime.now(UTC),
                payload={"summary": "Synthetic non-sensitive alert"},
            )
        )
        binding = await session.scalar(
            select(FeishuCardBinding).where(FeishuCardBinding.alert_id == result.alert.id)
        )
        assert binding is not None
        binding.message_id = "om_shared"
        delivery = await session.scalar(
            select(FeishuDelivery).where(FeishuDelivery.binding_id == binding.id)
        )
        assert delivery is not None
        await session.commit()
        return binding, delivery


def callback_payload(
    *,
    binding: FeishuCardBinding,
    delivery: FeishuDelivery,
    event_id: str = "event-start-1",
    action: str = "start",
    open_id: str = "ou_member",
    feedback: str | None = None,
    create_time: str | None = None,
    message_id: str = "om_shared",
    chat_id: str = "oc_test",
) -> dict[str, object]:
    action_payload: dict[str, object] = {
        "tag": "button",
        "value": {
            "binding_id": str(binding.id),
            "action": action,
            "revision": delivery.revision,
            "nonce": delivery.action_nonce,
        },
    }
    if feedback is not None:
        action_payload["form_value"] = {"feedback": feedback}
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "card.action.trigger",
            "create_time": create_time or str(int(time.time() * 1000)),
            "token": VERIFICATION_TOKEN,
            "app_id": "cli_test",
            "tenant_key": "tenant-test",
        },
        "event": {
            "operator": {"open_id": open_id},
            "token": "synthetic-action-token",
            "context": {"open_message_id": message_id, "open_chat_id": chat_id},
            "action": action_payload,
        },
    }


@pytest.mark.asyncio
async def test_unsigned_encrypted_challenge_is_verified(
    api_client: AsyncClient,
) -> None:
    settings = api_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        body = encrypt(
            {
                "challenge": "synthetic-challenge",
                "token": VERIFICATION_TOKEN,
                "type": "url_verification",
            }
        )
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers={"Content-Type": "application/json"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 200
    assert response.json() == {"challenge": "synthetic-challenge"}


@pytest.mark.asyncio
async def test_signed_encrypted_challenge_remains_supported(
    api_client: AsyncClient,
) -> None:
    settings = api_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        body = encrypt(
            {
                "challenge": "synthetic-signed-challenge",
                "token": VERIFICATION_TOKEN,
                "type": "url_verification",
            }
        )
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=signed_headers(body, int(time.time())),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 200
    assert response.json() == {"challenge": "synthetic-signed-challenge"}


@pytest.mark.asyncio
async def test_unsigned_business_callback_is_rejected_and_logged(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    body = encrypt(callback_payload(binding=binding, delivery=delivery))
    app.dependency_overrides[get_settings] = lambda: settings
    caplog.set_level(logging.WARNING, logger="app.api.v1.routes.feishu")
    try:
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers={"Content-Type": "application/json"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_signature_headers"
    assert any(
        getattr(record, "error_code", None) == "missing_signature_headers"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_callback_rejects_bad_signature_and_oversized_body(
    api_client: AsyncClient,
) -> None:
    settings = api_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    body = encrypt(
        {
            "challenge": "synthetic-challenge",
            "token": VERIFICATION_TOKEN,
            "type": "url_verification",
        }
    )
    try:
        bad_headers = signed_headers(body, int(time.time()))
        bad_headers["X-Lark-Signature"] = "0" * 64
        bad_signature = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=bad_headers,
        )
        oversized = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=b"x" * (settings.feishu_callback_max_bytes + 1),
            headers={"Content-Type": "application/json"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert bad_signature.status_code == 401
    assert bad_signature.json()["error"]["code"] == "invalid_signature"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "feishu_callback_too_large"


@pytest.mark.asyncio
async def test_start_callback_is_atomic_and_replay_safe(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    payload = callback_payload(binding=binding, delivery=delivery)
    body = encrypt(payload)
    headers = signed_headers(body, "2026-08-11T07:23:34Z")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        first = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=headers,
        )
        replay = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=signed_headers(body, "2026-08-11T07:23:35Z"),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.headers["x-idempotent-replay"] == "true"
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(FeishuCallbackEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(WorkflowRun)) == 1
        workflow_outbox = await session.scalar(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.topic == OutboxTopic.WORKFLOW_START)
        )
        assert workflow_outbox == 1


@pytest.mark.asyncio
async def test_business_callback_rejects_stale_signed_event_time(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    body = encrypt(
        callback_payload(
            binding=binding,
            delivery=delivery,
            event_id="event-stale-1",
            create_time=str(int((time.time() - 301) * 1_000_000)),
        )
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=signed_headers(body, "opaque-signed-header-value"),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "stale_callback"


@pytest.mark.asyncio
async def test_business_callback_rejects_missing_action_token_without_logging_values(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    payload = callback_payload(binding=binding, delivery=delivery, event_id="event-no-token")
    assert isinstance(payload["event"], dict)
    payload["event"].pop("token")
    body = encrypt(payload)
    app.dependency_overrides[get_settings] = lambda: settings
    caplog.set_level(logging.WARNING, logger="app.api.v1.routes.feishu")
    try:
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=signed_headers(body, "opaque-signed-header-value"),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 422
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "feishu.callback.schema_rejected"
    ]
    assert len(records) == 1
    assert records[0].error_fields == ["event.token"]
    assert "synthetic-action-token" not in caplog.text


@pytest.mark.asyncio
async def test_same_event_id_with_different_body_is_rejected(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    first_body = encrypt(callback_payload(binding=binding, delivery=delivery))
    changed_body = encrypt(callback_payload(binding=binding, delivery=delivery, action="retry"))
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        assert (
            await api_client.post(
                "/api/v1/integrations/feishu/card-actions",
                content=first_body,
                headers=signed_headers(first_body, int(time.time())),
            )
        ).status_code == 200
        rejected = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=changed_body,
            headers=signed_headers(changed_body, int(time.time())),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "event_id_body_mismatch"


@pytest.mark.asyncio
async def test_approval_requires_configured_open_id_and_is_audited(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    async with test_session_factory() as session:
        alert = await session.get(Alert, binding.alert_id)
        assert alert is not None
        alert.status = AlertStatus.WAITING_FOR_APPROVAL
        run = WorkflowRun(
            id=uuid4(),
            alert_id=alert.id,
            thread_id=str(uuid4()),
            idempotency_key="waiting-run",
            workflow_version="1.0",
            status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
            current_node="human_review",
        )
        session.add(run)
        await session.flush()
        session.add(
            DiagnosisReport(
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
        )
        await session.commit()
    body = encrypt(
        callback_payload(
            binding=binding,
            delivery=delivery,
            event_id="event-unauthorized-approve",
            action="approve",
            open_id="ou_not_allowed",
        )
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=signed_headers(body, int(time.time())),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 200
    assert "没有" in response.json()["toast"]["content"]
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(HumanDecision)) == 0
        audit = await session.scalar(
            select(FeishuCallbackEvent).where(
                FeishuCallbackEvent.event_id == "event-unauthorized-approve"
            )
        )
        assert audit is not None
        assert audit.status == FeishuCallbackStatus.REJECTED
        assert audit.error_code == "actor_not_authorized"


@pytest.mark.asyncio
async def test_private_reminder_action_requires_matching_recipient_and_schedules_resolution(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    async with test_session_factory() as session:
        alert = await session.get(Alert, binding.alert_id)
        assert alert is not None
        alert.status = AlertStatus.WAITING_FOR_APPROVAL
        run = WorkflowRun(
            id=uuid4(),
            alert_id=alert.id,
            thread_id=str(uuid4()),
            idempotency_key="private-reminder-run",
            workflow_version="1.0",
            status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
            current_node="human_review",
        )
        session.add(run)
        await session.flush()
        session.add(
            DiagnosisReport(
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
        )
        session.add(
            FeishuDelivery(
                binding_id=binding.id,
                kind=FeishuDeliveryKind.PRIVATE_REMINDER,
                recipient_open_id="ou_approver",
                revision=delivery.revision,
                action_nonce=delivery.action_nonce,
                idempotency_key="private-reminder-action",
                status=FeishuDeliveryStatus.SUCCEEDED,
                attempts=1,
                message_id="om_private",
            )
        )
        await session.commit()

    body = encrypt(
        callback_payload(
            binding=binding,
            delivery=delivery,
            event_id="event-private-reject",
            action="reject",
            open_id="ou_approver",
            message_id="om_private",
            chat_id="oc_private",
        )
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=signed_headers(body, int(time.time())),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert response.json()["toast"]["content"] == "人工决策已接收"
    async with test_session_factory() as session:
        decision = await session.scalar(select(HumanDecision))
        assert decision is not None and str(decision.action) == "reject"
        resolution = await session.scalar(
            select(FeishuDelivery).where(FeishuDelivery.decision_id == decision.id)
        )
        assert resolution is not None
        assert resolution.message_id == "om_private"
        assert resolution.recipient_open_id == "ou_approver"


@pytest.mark.asyncio
async def test_private_reminder_action_rejects_different_operator(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = api_settings()
    binding, delivery = await create_binding(test_session_factory, settings)
    async with test_session_factory() as session:
        session.add(
            FeishuDelivery(
                binding_id=binding.id,
                kind=FeishuDeliveryKind.PRIVATE_REMINDER,
                recipient_open_id="ou_approver",
                revision=delivery.revision,
                action_nonce=delivery.action_nonce,
                idempotency_key="private-reminder-wrong-operator",
                status=FeishuDeliveryStatus.SUCCEEDED,
                attempts=1,
                message_id="om_private",
            )
        )
        await session.commit()
    body = encrypt(
        callback_payload(
            binding=binding,
            delivery=delivery,
            event_id="event-private-wrong-operator",
            action="approve",
            open_id="ou_not_recipient",
            message_id="om_private",
            chat_id="oc_private",
        )
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = await api_client.post(
            "/api/v1/integrations/feishu/card-actions",
            content=body,
            headers=signed_headers(body, int(time.time())),
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert "过期" in response.json()["toast"]["content"]
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(HumanDecision)) == 0
