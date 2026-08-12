import hashlib
import hmac
import logging
from time import monotonic
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.session import get_db_session, get_session_factory
from app.integrations.feishu.callback import (
    FeishuCallbackRejectedError,
    FeishuCallbackService,
)
from app.integrations.feishu.coordination import (
    alert_feishu_eligibility,
    schedule_card_sync,
)
from app.integrations.feishu.schemas import (
    FeishuActionCommand,
    FeishuActionValue,
    FeishuCardCallback,
    FeishuChallenge,
)
from app.integrations.feishu.security import (
    FeishuCallbackSecurityError,
    decrypt_callback,
    validate_callback_timestamp,
    verify_callback_signature,
)
from app.models.alert import Alert
from app.models.enums import FeishuCardStatus
from app.models.feishu import FeishuCardBinding
from app.observability.metrics import observe_feishu_callback
from app.schemas.feishu import FeishuChannelStatusResponse, FeishuRetryResponse

router = APIRouter()
integrations_router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
AppSettings = Annotated[Settings, Depends(get_settings)]
logger = logging.getLogger(__name__)


@integrations_router.post("/feishu/card-actions", include_in_schema=True)
async def feishu_card_actions(
    request: Request,
    session_factory: SessionFactory,
    settings: AppSettings,
    timestamp: Annotated[str | None, Header(alias="X-Lark-Request-Timestamp")] = None,
    nonce: Annotated[str | None, Header(alias="X-Lark-Request-Nonce")] = None,
    signature: Annotated[str | None, Header(alias="X-Lark-Signature")] = None,
) -> JSONResponse:
    started = monotonic()
    if not settings.feishu_enabled:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="feishu_disabled",
            message="Feishu integration is not enabled",
        )
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.feishu_callback_max_bytes:
                raise _payload_too_large()
        except ValueError:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_content_length",
                message="invalid Content-Length header",
            ) from None
    raw_body = await request.body()
    if len(raw_body) > settings.feishu_callback_max_bytes:
        raise _payload_too_large()
    encrypt_key = settings.feishu_encrypt_key
    if encrypt_key is None:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="feishu_configuration_error",
            message="Feishu callback security is not configured",
        )
    signature_headers = (timestamp, nonce, signature)
    has_any_signature_header = any(value is not None for value in signature_headers)
    try:
        if has_any_signature_header:
            verify_callback_signature(
                raw_body=raw_body,
                timestamp=timestamp,
                nonce=nonce,
                signature=signature,
                encrypt_key=encrypt_key.get_secret_value(),
            )
        payload = decrypt_callback(raw_body, encrypt_key=encrypt_key.get_secret_value())
        # Feishu's URL verification request is explicitly excluded from callback
        # signature verification and may omit every X-Lark signature header. It is
        # still encrypted and bound to this app by the Verification Token below.
        if not has_any_signature_header and payload.get("type") != "url_verification":
            raise FeishuCallbackSecurityError("missing_signature_headers")
    except FeishuCallbackSecurityError as exc:
        observe_feishu_callback(
            result="security_rejected",
            duration_seconds=max(0.0, monotonic() - started),
        )
        logger.warning(
            "feishu.callback.security_rejected",
            extra={"error_code": exc.code},
        )
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=exc.code,
            message="Feishu callback authentication failed",
        ) from exc

    if payload.get("type") == "url_verification":
        try:
            challenge = FeishuChallenge.model_validate(payload)
        except ValidationError as exc:
            raise _invalid_callback() from exc
        verification = settings.feishu_verification_token
        if verification is None or not hmac.compare_digest(
            challenge.token, verification.get_secret_value()
        ):
            observe_feishu_callback(
                result="security_rejected",
                duration_seconds=max(0.0, monotonic() - started),
            )
            logger.warning(
                "feishu.callback.security_rejected",
                extra={"error_code": "invalid_verification_token"},
            )
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="invalid_verification_token",
                message="Feishu verification token is invalid",
            )
        observe_feishu_callback(
            result="challenge",
            duration_seconds=max(0.0, monotonic() - started),
        )
        return JSONResponse({"challenge": challenge.challenge})

    if not has_any_signature_header:
        # The only unsigned request admitted above is URL verification, which has
        # already returned. Keep this guard close to the business processing path.
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="missing_signature_headers",
            message="Feishu callback authentication failed",
        )

    try:
        callback = FeishuCardCallback.model_validate(payload)
    except ValidationError as exc:
        _log_schema_rejection(exc, stage="callback")
        raise _invalid_callback() from exc
    try:
        # Feishu specifies the signature timestamp as an opaque header value.
        # Freshness is enforced with the encrypted, signed event create_time.
        request_timestamp = validate_callback_timestamp(
            callback.header.create_time,
            max_age_seconds=settings.feishu_callback_max_age_seconds,
        )
    except FeishuCallbackSecurityError as exc:
        observe_feishu_callback(
            result="security_rejected",
            duration_seconds=max(0.0, monotonic() - started),
        )
        logger.warning(
            "feishu.callback.security_rejected",
            extra={"error_code": exc.code},
        )
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=exc.code,
            message="Feishu callback authentication failed",
        ) from exc
    try:
        action_value = FeishuActionValue.model_validate(callback.event.action.value)
        feedback = _feedback(callback.event.action.form_value, callback.event.action.input_value)
        command = FeishuActionCommand(
            callback=callback,
            value=action_value,
            feedback=feedback,
        )
    except ValidationError as exc:
        _log_schema_rejection(exc, stage="action")
        raise _invalid_callback() from exc
    try:
        result = await FeishuCallbackService(
            session_factory=session_factory,
            settings=settings,
        ).process(
            command=command,
            raw_body_sha256=hashlib.sha256(raw_body).hexdigest(),
            request_timestamp=request_timestamp,
        )
    except FeishuCallbackRejectedError as exc:
        observe_feishu_callback(
            result="rejected",
            duration_seconds=max(0.0, monotonic() - started),
        )
        raise ApiError(
            status_code=exc.status_code,
            code=exc.code,
            message="Feishu callback was rejected",
        ) from exc
    response = JSONResponse(result.payload)
    observe_feishu_callback(
        result="duplicate" if result.duplicate else "processed",
        duration_seconds=max(0.0, monotonic() - started),
    )
    response.headers["X-Idempotent-Replay"] = str(result.duplicate).lower()
    return response


@router.get("/{alert_id}/feishu", response_model=FeishuChannelStatusResponse)
async def get_feishu_status(
    alert_id: UUID,
    session: DatabaseSession,
    settings: AppSettings,
) -> FeishuChannelStatusResponse:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise ApiError(status_code=404, code="alert_not_found", message="alert does not exist")
    eligibility = alert_feishu_eligibility(alert, settings)
    binding = await session.scalar(
        select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert_id)
    )
    return FeishuChannelStatusResponse(
        alert_id=alert.id,
        enabled=settings.feishu_enabled,
        eligible=eligibility.eligible,
        eligibility_reason=eligibility.reason,
        binding_id=binding.id if binding else None,
        shared_card_status=binding.status if binding else None,
        desired_revision=binding.desired_revision if binding else None,
        delivered_revision=binding.delivered_revision if binding else None,
        last_error_code=binding.last_error_code if binding else None,
        retry_available=bool(binding and binding.status == FeishuCardStatus.FAILED),
    )


@router.post("/{alert_id}/feishu/retry", response_model=FeishuRetryResponse)
async def retry_feishu_delivery(
    alert_id: UUID,
    session: DatabaseSession,
    settings: AppSettings,
) -> FeishuRetryResponse:
    if not settings.feishu_enabled:
        raise ApiError(
            status_code=409,
            code="feishu_disabled",
            message="Feishu integration is not enabled",
        )
    binding = await session.scalar(
        select(FeishuCardBinding).where(FeishuCardBinding.alert_id == alert_id).with_for_update()
    )
    if binding is None:
        raise ApiError(status_code=404, code="feishu_binding_not_found", message="card not found")
    if binding.status != FeishuCardStatus.FAILED:
        raise ApiError(
            status_code=409,
            code="feishu_retry_not_available",
            message="the latest card delivery is not failed",
        )
    delivery = await schedule_card_sync(
        session,
        alert_id=alert_id,
        reason=f"manual-channel-retry-{binding.desired_revision + 1}",
        settings=settings,
    )
    await session.commit()
    return FeishuRetryResponse(
        alert_id=alert_id,
        delivery_id=delivery.id if delivery else None,
        revision=delivery.revision if delivery else None,
        dispatched=delivery is not None,
    )


def _feedback(form_value: dict[str, Any] | None, input_value: str | None) -> str | None:
    value = (form_value or {}).get("feedback", input_value)
    return value if isinstance(value, str) else None


def _invalid_callback() -> ApiError:
    return ApiError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_feishu_callback",
        message="Feishu callback schema validation failed",
    )


def _log_schema_rejection(exc: ValidationError, *, stage: str) -> None:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)[:8]
    logger.warning(
        "feishu.callback.schema_rejected",
        extra={
            "validation_stage": stage,
            "error_fields": [".".join(str(part) for part in error["loc"]) for error in errors],
            "error_types": [str(error["type"]) for error in errors],
        },
    )


def _payload_too_large() -> ApiError:
    return ApiError(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        code="feishu_callback_too_large",
        message="Feishu callback exceeds the configured size limit",
    )
