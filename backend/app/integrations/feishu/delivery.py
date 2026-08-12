from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.feishu.cards import (
    FeishuCardView,
    render_private_reminder,
    render_private_resolution,
    render_shared_card,
)
from app.integrations.feishu.client import FeishuApiError, FeishuClient
from app.models.alert import Alert
from app.models.case import Case
from app.models.diagnosis import DiagnosisReport, HumanDecision
from app.models.enums import (
    AlertStatus,
    FeishuCardStatus,
    FeishuDeliveryKind,
    FeishuDeliveryStatus,
)
from app.models.feishu import FeishuCardBinding, FeishuDelivery
from app.models.workflow import WorkflowRun


class FeishuDeliveryNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FeishuDeliveryResult:
    delivery_id: UUID
    status: FeishuDeliveryStatus
    skipped_stale: bool


class FeishuDeliveryService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        client: FeishuClient,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.client = client
        self.settings = settings

    async def execute(self, delivery_id: UUID) -> FeishuDeliveryResult:
        delivery, binding = await self._mark_processing(delivery_id)
        if delivery.status == FeishuDeliveryStatus.SUCCEEDED:
            return FeishuDeliveryResult(delivery.id, delivery.status, False)
        if (
            delivery.kind == FeishuDeliveryKind.CARD_SYNC
            and delivery.revision < binding.desired_revision
        ):
            await self._mark_succeeded(delivery.id, message_id=None, stale=True)
            return FeishuDeliveryResult(delivery.id, FeishuDeliveryStatus.SUCCEEDED, True)
        if (
            delivery.kind == FeishuDeliveryKind.PRIVATE_REMINDER
            and delivery.decision_id is None
            and delivery.revision < binding.desired_revision
        ):
            await self._mark_succeeded(delivery.id, message_id=None, stale=True)
            return FeishuDeliveryResult(delivery.id, FeishuDeliveryStatus.SUCCEEDED, True)
        view = await self._load_view(delivery, binding)
        try:
            if delivery.kind == FeishuDeliveryKind.PRIVATE_REMINDER:
                if not delivery.recipient_open_id:
                    raise FeishuApiError("reminder_recipient_missing")
                if delivery.decision_id is not None:
                    if not delivery.message_id:
                        raise FeishuApiError("reminder_message_missing")
                    try:
                        await self.client.update_card(
                            message_id=delivery.message_id,
                            card=render_private_resolution(view),
                        )
                        message_id = delivery.message_id
                    except FeishuApiError as exc:
                        if not exc.replace_message:
                            raise
                        message_id = await self.client.send_card(
                            receive_id=delivery.recipient_open_id,
                            receive_id_type="open_id",
                            card=render_private_resolution(view),
                        )
                else:
                    if view.status != AlertStatus.WAITING_FOR_APPROVAL or view.decision_action:
                        await self._mark_succeeded(delivery.id, message_id=None, stale=True)
                        return FeishuDeliveryResult(
                            delivery.id,
                            FeishuDeliveryStatus.SUCCEEDED,
                            True,
                        )
                    message_id = await self.client.send_card(
                        receive_id=delivery.recipient_open_id,
                        receive_id_type="open_id",
                        card=render_private_reminder(view),
                    )
            elif binding.message_id:
                try:
                    await self.client.update_card(
                        message_id=binding.message_id,
                        card=render_shared_card(view),
                    )
                    message_id = binding.message_id
                except FeishuApiError as exc:
                    if not exc.replace_message:
                        raise
                    message_id = await self.client.send_card(
                        receive_id=binding.chat_id,
                        receive_id_type="chat_id",
                        card=render_shared_card(view),
                    )
            else:
                message_id = await self.client.send_card(
                    receive_id=binding.chat_id,
                    receive_id_type="chat_id",
                    card=render_shared_card(view),
                )
        except FeishuApiError as exc:
            await self._mark_failed(delivery.id, exc.code)
            raise
        await self._mark_succeeded(delivery.id, message_id=message_id, stale=False)
        return FeishuDeliveryResult(delivery.id, FeishuDeliveryStatus.SUCCEEDED, False)

    async def _mark_processing(self, delivery_id: UUID) -> tuple[FeishuDelivery, FeishuCardBinding]:
        async with self.session_factory() as session:
            delivery = await session.scalar(
                select(FeishuDelivery).where(FeishuDelivery.id == delivery_id).with_for_update()
            )
            if delivery is None:
                raise FeishuDeliveryNotFoundError
            binding = await session.get(FeishuCardBinding, delivery.binding_id)
            if binding is None:
                raise FeishuDeliveryNotFoundError
            if delivery.status != FeishuDeliveryStatus.SUCCEEDED:
                delivery.status = FeishuDeliveryStatus.PROCESSING
                delivery.attempts += 1
                delivery.last_error_code = None
                await session.commit()
            return delivery, binding

    async def _load_view(
        self,
        delivery: FeishuDelivery,
        binding: FeishuCardBinding,
    ) -> FeishuCardView:
        async with self.session_factory() as session:
            alert = await session.get(Alert, binding.alert_id)
            if alert is None:
                raise FeishuDeliveryNotFoundError
            run = await session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.alert_id == alert.id)
                .order_by(WorkflowRun.created_at.desc())
                .limit(1)
            )
            report = None
            decision = None
            case = None
            if delivery.decision_id is not None:
                decision = await session.get(HumanDecision, delivery.decision_id)
                if decision is None:
                    raise FeishuDeliveryNotFoundError
                report = await session.get(DiagnosisReport, decision.diagnosis_report_id)
                if report is None:
                    raise FeishuDeliveryNotFoundError
                run = await session.get(WorkflowRun, report.workflow_run_id)
                if run is None or run.alert_id != alert.id:
                    raise FeishuDeliveryNotFoundError
            elif run is not None:
                report = await session.scalar(
                    select(DiagnosisReport)
                    .where(DiagnosisReport.workflow_run_id == run.id)
                    .order_by(DiagnosisReport.version.desc())
                    .limit(1)
                )
            if report is not None:
                decision = await session.scalar(
                    select(HumanDecision).where(HumanDecision.diagnosis_report_id == report.id)
                )
                case = await session.scalar(
                    select(Case).where(Case.diagnosis_report_id == report.id)
                )
            evidence = report.evidence if report else []
            return FeishuCardView(
                binding_id=binding.id,
                revision=delivery.revision,
                nonce=delivery.action_nonce,
                alert_id=alert.id,
                alert_name=alert.alert_name,
                service=alert.service,
                severity=str(alert.severity),
                status=alert.status,
                summary=report.summary if report else None,
                knowledge_missing=bool(report)
                and not any(item.get("type") in {"knowledge", "case"} for item in evidence),
                error_code=run.error_code if run else None,
                case_status=case.knowledge_sync_status if case else None,
                actor_label=(decision.actor_display_name or decision.actor) if decision else None,
                decision_action=str(decision.action) if decision else None,
                decision_at=decision.created_at if decision else None,
                web_url=f"{self.settings.feishu_web_base_url}/alerts/{alert.id}",
            )

    async def _mark_succeeded(
        self,
        delivery_id: UUID,
        *,
        message_id: str | None,
        stale: bool,
    ) -> None:
        async with self.session_factory() as session:
            delivery = await session.scalar(
                select(FeishuDelivery).where(FeishuDelivery.id == delivery_id).with_for_update()
            )
            if delivery is None:
                raise FeishuDeliveryNotFoundError
            binding = await session.scalar(
                select(FeishuCardBinding)
                .where(FeishuCardBinding.id == delivery.binding_id)
                .with_for_update()
            )
            if binding is None:
                raise FeishuDeliveryNotFoundError
            delivery.status = FeishuDeliveryStatus.SUCCEEDED
            delivery.message_id = message_id or delivery.message_id
            delivery.last_error_code = None
            if not stale and delivery.kind == FeishuDeliveryKind.CARD_SYNC:
                if delivery.revision == binding.desired_revision:
                    binding.delivered_revision = delivery.revision
                    binding.message_id = message_id or binding.message_id
                    binding.status = FeishuCardStatus.ACTIVE
                    binding.last_error_code = None
            await session.commit()

    async def _mark_failed(self, delivery_id: UUID, error_code: str) -> None:
        async with self.session_factory() as session:
            delivery = await session.scalar(
                select(FeishuDelivery).where(FeishuDelivery.id == delivery_id).with_for_update()
            )
            if delivery is None:
                return
            binding = await session.scalar(
                select(FeishuCardBinding)
                .where(FeishuCardBinding.id == delivery.binding_id)
                .with_for_update()
            )
            delivery.status = FeishuDeliveryStatus.FAILED
            delivery.last_error_code = error_code[:64]
            if (
                binding is not None
                and delivery.kind == FeishuDeliveryKind.CARD_SYNC
                and delivery.revision == binding.desired_revision
            ):
                binding.status = FeishuCardStatus.FAILED
                binding.last_error_code = error_code[:64]
            await session.commit()
