import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.feishu.coordination import nonce_matches
from app.integrations.feishu.schemas import FeishuActionCommand
from app.models.enums import (
    FeishuCallbackStatus,
    FeishuDeliveryKind,
    FeishuDeliveryStatus,
    HumanActorSource,
    HumanDecisionAction,
)
from app.models.feishu import FeishuCallbackEvent, FeishuCardBinding, FeishuDelivery
from app.models.workflow import WorkflowRun
from app.schemas.workflow import WorkflowResumePayload
from app.services.workflow_commands import (
    WorkflowCommandIdempotencyConflictError,
    WorkflowCommandNotFoundError,
    WorkflowCommandService,
    WorkflowCommandStateConflictError,
)


class FeishuCallbackRejectedError(ValueError):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class FeishuCallbackResult:
    payload: dict[str, object]
    duplicate: bool


class FeishuCallbackService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def process(
        self,
        *,
        command: FeishuActionCommand,
        raw_body_sha256: str,
        request_timestamp: int,
    ) -> FeishuCallbackResult:
        header = command.callback.header
        context = command.callback.event.context
        operator = command.callback.event.operator
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(FeishuCallbackEvent).where(
                    FeishuCallbackEvent.app_id == header.app_id,
                    FeishuCallbackEvent.event_id == header.event_id,
                )
            )
            if existing is not None:
                if not hmac.compare_digest(existing.raw_body_sha256, raw_body_sha256):
                    raise FeishuCallbackRejectedError("event_id_body_mismatch")
                return FeishuCallbackResult(existing.response_payload, True)

            binding = await session.scalar(
                select(FeishuCardBinding)
                .where(FeishuCardBinding.id == command.value.binding_id)
                .with_for_update()
            )
            audit = FeishuCallbackEvent(
                app_id=header.app_id,
                event_id=header.event_id,
                binding_id=binding.id if binding else None,
                open_id=operator.open_id,
                action=command.value.action,
                message_id=context.open_message_id,
                chat_id=context.open_chat_id,
                request_timestamp=request_timestamp,
                raw_body_sha256=raw_body_sha256,
            )
            try:
                async with session.begin_nested():
                    session.add(audit)
                    await session.flush()
            except IntegrityError:
                replay = await session.scalar(
                    select(FeishuCallbackEvent).where(
                        FeishuCallbackEvent.app_id == header.app_id,
                        FeishuCallbackEvent.event_id == header.event_id,
                    )
                )
                if replay is None or replay.raw_body_sha256 != raw_body_sha256:
                    raise FeishuCallbackRejectedError("event_id_body_mismatch") from None
                return FeishuCallbackResult(replay.response_payload, True)

            try:
                async with session.begin_nested():
                    self._validate_context(command)
                    if binding is None:
                        raise FeishuCallbackRejectedError("binding_not_found", status_code=404)
                    private_reminder = await session.scalar(
                        select(FeishuDelivery).where(
                            FeishuDelivery.binding_id == binding.id,
                            FeishuDelivery.kind == FeishuDeliveryKind.PRIVATE_REMINDER,
                            FeishuDelivery.decision_id.is_(None),
                            FeishuDelivery.status == FeishuDeliveryStatus.SUCCEEDED,
                            FeishuDelivery.message_id == context.open_message_id,
                            FeishuDelivery.recipient_open_id == operator.open_id,
                        )
                    )
                    self._validate_binding(binding, command, private_reminder)
                    payload = await self._apply_action(session, binding, command)
            except FeishuCallbackRejectedError as exc:
                audit.status = FeishuCallbackStatus.REJECTED
                audit.result_code = exc.code
                audit.error_code = exc.code
                audit.response_payload = self._toast("error", self._message_for_code(exc.code))
                audit.processed_at = datetime.now(UTC)
                await session.commit()
                return FeishuCallbackResult(audit.response_payload, False)
            except (
                WorkflowCommandStateConflictError,
                WorkflowCommandNotFoundError,
                WorkflowCommandIdempotencyConflictError,
            ) as exc:
                audit.status = FeishuCallbackStatus.REJECTED
                audit.result_code = "business_state_conflict"
                audit.error_code = "business_state_conflict"
                audit.response_payload = self._toast("warning", str(exc)[:200])
                audit.processed_at = datetime.now(UTC)
                await session.commit()
                return FeishuCallbackResult(audit.response_payload, False)
            except Exception as exc:
                audit.status = FeishuCallbackStatus.FAILED
                audit.result_code = "callback_processing_failed"
                audit.error_code = type(exc).__name__[:64]
                audit.response_payload = self._toast("error", "操作未完成，请稍后重试或使用 Web")
                audit.processed_at = datetime.now(UTC)
                await session.commit()
                raise

            audit.status = FeishuCallbackStatus.PROCESSED
            audit.result_code = "processed"
            audit.response_payload = payload
            audit.processed_at = datetime.now(UTC)
            await session.commit()
            return FeishuCallbackResult(payload, False)

    def _validate_context(self, command: FeishuActionCommand) -> None:
        header = command.callback.header
        token = self.settings.feishu_verification_token
        if header.app_id != self.settings.feishu_app_id:
            raise FeishuCallbackRejectedError("unexpected_app", status_code=403)
        if token is None or not hmac.compare_digest(header.token, token.get_secret_value()):
            raise FeishuCallbackRejectedError("invalid_verification_token", status_code=403)
        if header.tenant_key != self.settings.feishu_expected_tenant_key:
            raise FeishuCallbackRejectedError("unexpected_tenant", status_code=403)

    def _validate_binding(
        self,
        binding: FeishuCardBinding,
        command: FeishuActionCommand,
        private_reminder: FeishuDelivery | None,
    ) -> None:
        context = command.callback.event.context
        if binding.app_id != command.callback.header.app_id:
            raise FeishuCallbackRejectedError("binding_context_mismatch", status_code=403)
        shared_context = (
            binding.chat_id == context.open_chat_id
            and binding.message_id is not None
            and binding.message_id == context.open_message_id
        )
        if not shared_context and private_reminder is None:
            raise FeishuCallbackRejectedError("card_expired")
        if binding.desired_revision != command.value.revision:
            raise FeishuCallbackRejectedError("card_expired")
        if not nonce_matches(command.value.nonce, binding.action_nonce_hash):
            raise FeishuCallbackRejectedError("card_expired")

    async def _apply_action(
        self,
        session: AsyncSession,
        binding: FeishuCardBinding,
        command: FeishuActionCommand,
    ) -> dict[str, object]:
        action = command.value.action
        open_id = command.callback.event.operator.open_id
        service = WorkflowCommandService(session, settings=self.settings)
        if action == "start":
            prepared = await service.prepare_start(
                alert_id=binding.alert_id,
                idempotency_key=self._command_key(command),
            )
            message = "诊断已加入队列" if prepared.created else "诊断已启动，请勿重复操作"
            return self._toast("success", message)
        run = await session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.alert_id == binding.alert_id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if run is None:
            raise WorkflowCommandStateConflictError("alert has no workflow run")
        if action == "retry":
            created = await service.prepare_retry(run.id)
            return self._toast("success", "诊断重试已加入队列" if created else "重试已在队列中")
        label = self.settings.feishu_approvers.get(open_id)
        if label is None:
            raise FeishuCallbackRejectedError("actor_not_authorized", status_code=403)
        action_map = {
            "approve": HumanDecisionAction.APPROVE,
            "reject": HumanDecisionAction.REJECT,
            "reanalyze": HumanDecisionAction.REANALYZE,
        }
        decision = action_map[action]
        await service.prepare_decision(
            workflow_run_id=run.id,
            resume=WorkflowResumePayload(
                idempotency_key=self._command_key(command),
                action=decision,
                comment=command.feedback,
                actor=label,
                actor_source=HumanActorSource.FEISHU,
                actor_subject=open_id,
                actor_display_name=label,
            ),
        )
        return self._toast("success", "人工决策已接收")

    @staticmethod
    def _command_key(command: FeishuActionCommand) -> str:
        header = command.callback.header
        return f"feishu:{header.app_id}:{header.event_id}"[:160]

    @staticmethod
    def _toast(level: str, message: str) -> dict[str, object]:
        return {"toast": {"type": level, "content": message}}

    @staticmethod
    def _message_for_code(code: str) -> str:
        return {
            "actor_not_authorized": "你没有执行该人工决策的权限",
            "card_expired": "卡片已过期，请使用最新卡片",
            "binding_not_found": "卡片绑定不存在",
        }.get(code, "操作被拒绝，请使用最新卡片或 Web")
