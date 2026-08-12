import hashlib
import secrets
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.alert import Alert
from app.models.diagnosis import HumanDecision
from app.models.enums import (
    AlertSeverity,
    FeishuCardStatus,
    FeishuDeliveryKind,
    FeishuDeliveryStatus,
    OutboxTopic,
)
from app.models.feishu import FeishuCardBinding, FeishuDelivery
from app.services.outbox import enqueue_outbox_message


@dataclass(frozen=True, slots=True)
class FeishuEligibility:
    eligible: bool
    reason: str


def alert_feishu_eligibility(alert: Alert, settings: Settings) -> FeishuEligibility:
    if not settings.feishu_enabled:
        return FeishuEligibility(False, "disabled")
    if alert.severity not in {AlertSeverity.WARNING, AlertSeverity.CRITICAL}:
        return FeishuEligibility(False, "severity_not_eligible")
    allowed_sources = {item.casefold() for item in settings.feishu_source_allowlist}
    if alert.source.casefold() not in allowed_sources:
        return FeishuEligibility(False, "source_not_allowed")
    return FeishuEligibility(True, "eligible")


async def schedule_initial_card(
    session: AsyncSession,
    *,
    alert: Alert,
    settings: Settings,
) -> FeishuDelivery | None:
    if not alert_feishu_eligibility(alert, settings).eligible:
        return None
    binding = await session.scalar(
        select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert.id)
    )
    if binding is not None:
        return await session.scalar(
            select(FeishuDelivery).where(
                FeishuDelivery.idempotency_key == f"feishu:card:{binding.id}:alert-created"
            )
        )
    nonce = secrets.token_urlsafe(32)
    binding = FeishuCardBinding(
        alert_id=alert.id,
        app_id=settings.feishu_app_id or "",
        chat_id=settings.feishu_target_chat_id or "",
        desired_revision=1,
        delivered_revision=0,
        action_nonce_hash=_nonce_hash(nonce),
        status=FeishuCardStatus.PENDING,
    )
    session.add(binding)
    await session.flush()
    return await _create_delivery(
        session,
        binding=binding,
        kind=FeishuDeliveryKind.CARD_SYNC,
        revision=1,
        nonce=nonce,
        idempotency_key=f"feishu:card:{binding.id}:alert-created",
        recipient_open_id=None,
        alert_id=alert.id,
    )


async def schedule_card_sync(
    session: AsyncSession,
    *,
    alert_id: UUID,
    reason: str,
    settings: Settings,
) -> FeishuDelivery | None:
    if not settings.feishu_enabled:
        return None
    binding = await session.scalar(
        select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert_id).with_for_update()
    )
    if binding is None:
        return None
    key = f"feishu:card:{binding.id}:{reason}"[:200]
    existing = await session.scalar(
        select(FeishuDelivery).where(FeishuDelivery.idempotency_key == key)
    )
    if existing is not None:
        return existing
    binding.desired_revision += 1
    nonce = secrets.token_urlsafe(32)
    binding.action_nonce_hash = _nonce_hash(nonce)
    binding.status = FeishuCardStatus.PENDING
    binding.last_error_code = None
    return await _create_delivery(
        session,
        binding=binding,
        kind=FeishuDeliveryKind.CARD_SYNC,
        revision=binding.desired_revision,
        nonce=nonce,
        idempotency_key=key,
        recipient_open_id=None,
        alert_id=alert_id,
    )


async def schedule_approval_reminders(
    session: AsyncSession,
    *,
    alert_id: UUID,
    settings: Settings,
) -> list[FeishuDelivery]:
    if not settings.feishu_enabled:
        return []
    binding = await session.scalar(
        select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert_id).with_for_update()
    )
    if binding is None:
        return []
    card_delivery = await session.scalar(
        select(FeishuDelivery).where(
            FeishuDelivery.binding_id == binding.id,
            FeishuDelivery.revision == binding.desired_revision,
            FeishuDelivery.kind == FeishuDeliveryKind.CARD_SYNC,
        )
    )
    nonce = card_delivery.action_nonce if card_delivery else secrets.token_urlsafe(32)
    deliveries: list[FeishuDelivery] = []
    for open_id in sorted(settings.feishu_approvers):
        key = f"feishu:reminder:{binding.id}:{binding.desired_revision}:{open_id}"[:200]
        existing = await session.scalar(
            select(FeishuDelivery).where(FeishuDelivery.idempotency_key == key)
        )
        if existing is not None:
            deliveries.append(existing)
            continue
        deliveries.append(
            await _create_delivery(
                session,
                binding=binding,
                kind=FeishuDeliveryKind.PRIVATE_REMINDER,
                revision=binding.desired_revision,
                nonce=nonce,
                idempotency_key=key,
                recipient_open_id=open_id,
                alert_id=alert_id,
            )
        )
    return deliveries


async def schedule_private_reminder_resolutions(
    session: AsyncSession,
    *,
    alert_id: UUID,
    decision: HumanDecision,
    settings: Settings,
) -> list[FeishuDelivery]:
    if not settings.feishu_enabled:
        return []
    binding = await session.scalar(
        select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert_id).with_for_update()
    )
    if binding is None:
        return []
    reminders = list(
        await session.scalars(
            select(FeishuDelivery).where(
                FeishuDelivery.binding_id == binding.id,
                FeishuDelivery.revision == binding.desired_revision,
                FeishuDelivery.kind == FeishuDeliveryKind.PRIVATE_REMINDER,
                FeishuDelivery.decision_id.is_(None),
                FeishuDelivery.status == FeishuDeliveryStatus.SUCCEEDED,
                FeishuDelivery.message_id.is_not(None),
            )
        )
    )
    deliveries: list[FeishuDelivery] = []
    for reminder in reminders:
        if not reminder.recipient_open_id or not reminder.message_id:
            continue
        key = (f"feishu:reminder-resolve:{binding.id}:{decision.id}:{reminder.recipient_open_id}")[
            :200
        ]
        existing = await session.scalar(
            select(FeishuDelivery).where(FeishuDelivery.idempotency_key == key)
        )
        if existing is not None:
            deliveries.append(existing)
            continue
        deliveries.append(
            await _create_delivery(
                session,
                binding=binding,
                kind=FeishuDeliveryKind.PRIVATE_REMINDER,
                revision=reminder.revision,
                nonce=reminder.action_nonce,
                idempotency_key=key,
                recipient_open_id=reminder.recipient_open_id,
                alert_id=alert_id,
                message_id=reminder.message_id,
                decision_id=decision.id,
            )
        )
    return deliveries


async def _create_delivery(
    session: AsyncSession,
    *,
    binding: FeishuCardBinding,
    kind: FeishuDeliveryKind,
    revision: int,
    nonce: str,
    idempotency_key: str,
    recipient_open_id: str | None,
    alert_id: UUID,
    message_id: str | None = None,
    decision_id: UUID | None = None,
) -> FeishuDelivery:
    delivery = FeishuDelivery(
        binding_id=binding.id,
        kind=kind,
        recipient_open_id=recipient_open_id,
        decision_id=decision_id,
        revision=revision,
        action_nonce=nonce,
        idempotency_key=idempotency_key,
        message_id=message_id,
    )
    session.add(delivery)
    await session.flush()
    topic = (
        OutboxTopic.FEISHU_CARD_SYNC
        if kind is FeishuDeliveryKind.CARD_SYNC
        else OutboxTopic.FEISHU_REMINDER_SEND
    )
    await enqueue_outbox_message(
        session,
        topic=topic,
        aggregate_id=delivery.id,
        idempotency_key=f"outbox:{idempotency_key}",
        payload={"delivery_id": str(delivery.id)},
        correlation={"alert_id": alert_id, "feishu_delivery_id": delivery.id},
    )
    return delivery


def nonce_matches(nonce: str, expected_hash: str) -> bool:
    return secrets.compare_digest(_nonce_hash(nonce), expected_hash)


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()
