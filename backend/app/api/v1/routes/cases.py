from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import get_db_session, get_session_factory
from app.integrations.knowledge.cases import MockCasePublisher
from app.models.enums import KnowledgeSyncStatus
from app.observability.logging import bind_log_context
from app.schemas.case import CaseResponse, CaseSyncAcceptedResponse
from app.schemas.error import ApiErrorResponse
from app.services.cases import (
    CaseNotFoundError,
    CaseQueryService,
    CaseStateConflictError,
    CaseSyncService,
)
from app.tasks.dispatcher import CaseDispatcher, get_case_dispatcher

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
Dispatcher = Annotated[CaseDispatcher, Depends(get_case_dispatcher)]
ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
}


def _case_not_found(alert_id: UUID) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="case_not_found",
        message="alert has no approved case",
        context={"alert_id": str(alert_id)},
    )


@router.get(
    "/{alert_id}/case",
    response_model=CaseResponse,
    responses=ERROR_RESPONSES,
)
async def get_case(alert_id: UUID, session: DatabaseSession) -> CaseResponse:
    try:
        case = await CaseQueryService(session).get_for_alert(alert_id)
    except CaseNotFoundError as exc:
        raise _case_not_found(alert_id) from exc
    return CaseResponse.model_validate(case)


@router.post(
    "/{alert_id}/case/retry",
    response_model=CaseSyncAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERROR_RESPONSES,
)
async def retry_case_sync(
    alert_id: UUID,
    session: DatabaseSession,
    session_factory: SessionFactory,
    dispatcher: Dispatcher,
) -> CaseSyncAcceptedResponse:
    try:
        case = await CaseQueryService(session).get_for_alert(alert_id)
        should_dispatch = await CaseSyncService(
            session_factory=session_factory,
            publisher=MockCasePublisher(),
            timeout_seconds=get_settings().case_sync_timeout_seconds,
        ).prepare_retry(case.id)
    except CaseNotFoundError as exc:
        raise _case_not_found(alert_id) from exc
    except CaseStateConflictError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="case_sync_state_conflict",
            message=str(exc),
            context={"alert_id": str(alert_id), "case_id": str(case.id)},
        ) from exc
    if should_dispatch:
        with bind_log_context(alert_id=alert_id, case_id=case.id):
            try:
                dispatcher.sync(case.id)
            except Exception as exc:
                raise ApiError(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    code="case_sync_dispatch_failed",
                    message="case retry was stored but task dispatch failed; retry is available",
                    context={"alert_id": str(alert_id), "case_id": str(case.id)},
                ) from exc
    return CaseSyncAcceptedResponse(
        case_id=case.id,
        status=(KnowledgeSyncStatus.PENDING if should_dispatch else case.knowledge_sync_status),
        dispatched=should_dispatch,
    )
