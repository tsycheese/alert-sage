import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import get_db_session, get_session_factory
from app.models.enums import WorkflowRunStatus
from app.schemas.error import ApiErrorResponse
from app.schemas.workflow import (
    DiagnosisReportResponse,
    HumanDecisionRequest,
    HumanDecisionResponse,
    WorkflowAcceptedResponse,
    WorkflowDetailResponse,
    WorkflowEventListResponse,
    WorkflowEventResponse,
    WorkflowRunResponse,
    WorkflowStartCommand,
)
from app.services.workflows import AlertWorkflowNotFoundError, WorkflowQueryService
from app.workflows.alert.service import (
    AlertWorkflowService,
    WorkflowIdempotencyConflictError,
    WorkflowRunNotFoundError,
    WorkflowStateConflictError,
)

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
}
TERMINAL_STATUSES = {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.REJECTED}


def _workflow_error(exc: Exception, alert_id: UUID) -> ApiError:
    if isinstance(exc, (WorkflowRunNotFoundError, AlertWorkflowNotFoundError)):
        return ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="workflow_not_found",
            message="alert or workflow does not exist",
            context={"alert_id": str(alert_id)},
        )
    code = (
        "workflow_idempotency_conflict"
        if isinstance(exc, WorkflowIdempotencyConflictError)
        else "workflow_state_conflict"
    )
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code=code,
        message=str(exc) or code.replace("_", " "),
        context={"alert_id": str(alert_id)},
    )


@router.post(
    "/{alert_id}/workflow",
    response_model=WorkflowAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERROR_RESPONSES,
)
async def start_workflow(
    alert_id: UUID,
    command: WorkflowStartCommand,
    response: Response,
    session: DatabaseSession,
    session_factory: SessionFactory,
) -> WorkflowAcceptedResponse:
    service = AlertWorkflowService(session_factory=session_factory, graph=None)
    try:
        prepared = await service.prepare_start(
            alert_id=alert_id,
            idempotency_key=command.idempotency_key,
        )
    except (
        WorkflowRunNotFoundError,
        WorkflowStateConflictError,
        WorkflowIdempotencyConflictError,
    ) as exc:
        raise _workflow_error(exc, alert_id) from exc
    response.headers["Location"] = f"/api/v1/alerts/{alert_id}/workflow"
    run = await WorkflowQueryService(session).latest_run(alert_id)
    return WorkflowAcceptedResponse(
        workflow_run_id=prepared.workflow_run_id,
        status=run.status,
        dispatched=prepared.created,
    )


@router.get(
    "/{alert_id}/workflow",
    response_model=WorkflowDetailResponse,
    responses=ERROR_RESPONSES,
)
async def get_workflow(alert_id: UUID, session: DatabaseSession) -> WorkflowDetailResponse:
    try:
        detail = await WorkflowQueryService(session).detail(alert_id)
    except AlertWorkflowNotFoundError as exc:
        raise _workflow_error(exc, alert_id) from exc
    return WorkflowDetailResponse(
        run=WorkflowRunResponse.model_validate(detail.run),
        report=(DiagnosisReportResponse.model_validate(detail.report) if detail.report else None),
        decision=(
            HumanDecisionResponse.model_validate(detail.decision) if detail.decision else None
        ),
    )


@router.get(
    "/{alert_id}/events",
    response_model=WorkflowEventListResponse,
    responses=ERROR_RESPONSES,
)
async def list_workflow_events(
    alert_id: UUID,
    session: DatabaseSession,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> WorkflowEventListResponse:
    try:
        _run, events = await WorkflowQueryService(session).events(
            alert_id, after=after, limit=limit
        )
    except AlertWorkflowNotFoundError as exc:
        raise _workflow_error(exc, alert_id) from exc
    items = [WorkflowEventResponse.model_validate(event) for event in events]
    return WorkflowEventListResponse(
        items=items,
        last_sequence=items[-1].sequence if items else after,
    )


@router.post(
    "/{alert_id}/decisions",
    response_model=WorkflowAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERROR_RESPONSES,
)
async def submit_workflow_decision(
    alert_id: UUID,
    command: HumanDecisionRequest,
    session: DatabaseSession,
    session_factory: SessionFactory,
) -> WorkflowAcceptedResponse:
    try:
        run = await WorkflowQueryService(session).latest_run(alert_id)
        decision_id = await AlertWorkflowService(
            session_factory=session_factory, graph=None
        ).prepare_resume(
            workflow_run_id=run.id,
            command=command,
            actor=command.actor,
        )
    except (
        AlertWorkflowNotFoundError,
        WorkflowRunNotFoundError,
        WorkflowStateConflictError,
        WorkflowIdempotencyConflictError,
    ) as exc:
        raise _workflow_error(exc, alert_id) from exc
    await session.refresh(run)
    return WorkflowAcceptedResponse(
        workflow_run_id=run.id,
        status=run.status,
        dispatched=decision_id is not None,
    )


@router.post(
    "/{alert_id}/retry",
    response_model=WorkflowAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERROR_RESPONSES,
)
async def retry_workflow(
    alert_id: UUID,
    session: DatabaseSession,
    session_factory: SessionFactory,
) -> WorkflowAcceptedResponse:
    try:
        run = await WorkflowQueryService(session).latest_run(alert_id)
        delivery_created = await AlertWorkflowService(
            session_factory=session_factory, graph=None
        ).prepare_retry(run.id)
    except (
        AlertWorkflowNotFoundError,
        WorkflowRunNotFoundError,
        WorkflowStateConflictError,
    ) as exc:
        raise _workflow_error(exc, alert_id) from exc
    return WorkflowAcceptedResponse(
        workflow_run_id=run.id,
        status=WorkflowRunStatus.QUEUED,
        dispatched=delivery_created,
    )


@router.get("/{alert_id}/stream", responses=ERROR_RESPONSES)
async def stream_workflow_events(
    alert_id: UUID,
    request: Request,
    session_factory: SessionFactory,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        async with session_factory() as initial_session:
            run = await WorkflowQueryService(initial_session).latest_run(alert_id)
    except AlertWorkflowNotFoundError as exc:
        raise _workflow_error(exc, alert_id) from exc
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    settings = get_settings()

    async def event_source() -> AsyncIterator[str]:
        nonlocal cursor
        redis: Redis | None = Redis.from_url(settings.redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(f"alert-sage:workflow:{run.id}")
        except Exception:
            await pubsub.aclose()
            await redis.aclose()
            pubsub = None
            redis = None
        try:
            while not await request.is_disconnected():
                async with session_factory() as event_session:
                    query_service = WorkflowQueryService(event_session)
                    current_run, events = await query_service.events(
                        alert_id, after=cursor, limit=200
                    )
                    case_sync_active = await query_service.case_sync_active(current_run.id)
                for event in events:
                    payload = WorkflowEventResponse.model_validate(event)
                    cursor = payload.sequence
                    yield (
                        f"id: {payload.sequence}\n"
                        f"event: {payload.event_type.value}\n"
                        f"data: {payload.model_dump_json()}\n\n"
                    )
                if current_run.status in TERMINAL_STATUSES and not events and not case_sync_active:
                    break
                if pubsub is not None:
                    try:
                        await pubsub.get_message(
                            ignore_subscribe_messages=True,
                            timeout=settings.workflow_event_poll_seconds,
                        )
                    except Exception:
                        await pubsub.aclose()
                        if redis is not None:
                            await redis.aclose()
                        pubsub = None
                        redis = None
                else:
                    await asyncio.sleep(settings.workflow_event_poll_seconds)
                yield ": keep-alive\n\n"
                await asyncio.sleep(0)
        finally:
            if pubsub is not None:
                await pubsub.aclose()
            if redis is not None:
                await redis.aclose()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
