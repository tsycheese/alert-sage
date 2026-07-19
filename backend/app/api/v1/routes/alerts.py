from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.session import get_db_session
from app.models.enums import AlertSeverity, AlertStatus
from app.schemas.alert import AlertCreate, AlertListResponse, AlertResponse
from app.schemas.error import ApiErrorResponse
from app.services.alerts import (
    AlertIdempotencyConflictError,
    AlertNotFoundError,
    AlertService,
)

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
VALIDATION_RESPONSE = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ApiErrorResponse,
        "description": "Request validation failed.",
    }
}


@router.post(
    "",
    response_model=AlertResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": AlertResponse,
            "description": "Idempotent replay; the existing alert is returned.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ApiErrorResponse,
            "description": "The idempotency key was reused with different content.",
        },
        **VALIDATION_RESPONSE,
    },
)
async def create_alert(
    command: AlertCreate,
    response: Response,
    session: DatabaseSession,
) -> AlertResponse:
    try:
        result = await AlertService(session).create(command)
    except AlertIdempotencyConflictError as exc:
        href = f"/api/v1/alerts/{exc.alert_id}"
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="alert_idempotency_conflict",
            message="source and external_alert_id already exist with different content",
            context={
                "alert_id": str(exc.alert_id),
                "href": href,
            },
            headers={"Location": href},
        ) from exc

    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    response.headers["Location"] = f"/api/v1/alerts/{result.alert.id}"
    response.headers["X-Idempotent-Replay"] = str(not result.created).lower()
    return AlertResponse.model_validate(result.alert)


@router.get("", response_model=AlertListResponse, responses=VALIDATION_RESPONSE)
async def list_alerts(
    session: DatabaseSession,
    alert_status: Annotated[AlertStatus | None, Query(alias="status")] = None,
    severity: AlertSeverity | None = None,
    service: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AlertListResponse:
    result = await AlertService(session).list(
        status=alert_status,
        severity=severity,
        service=service,
        page=page,
        page_size=page_size,
    )
    return AlertListResponse(
        items=[AlertResponse.model_validate(alert) for alert in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        pages=result.pages,
    )


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ApiErrorResponse,
            "description": "Alert not found.",
        },
        **VALIDATION_RESPONSE,
    },
)
async def get_alert(alert_id: UUID, session: DatabaseSession) -> AlertResponse:
    try:
        alert = await AlertService(session).get(alert_id)
    except AlertNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="alert_not_found",
            message="alert does not exist",
            context={"alert_id": str(alert_id)},
        ) from exc
    return AlertResponse.model_validate(alert)
