from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import get_db_session, get_session_factory
from app.schemas.error import ApiErrorResponse
from app.schemas.rag_evaluation import (
    RagEvaluationAcceptedResponse,
    RagEvaluationDetailResponse,
    RagEvaluationResultResponse,
    RagEvaluationRunCreate,
    RagEvaluationRunListResponse,
    RagEvaluationRunResponse,
)
from app.services.rag_evaluations import (
    RagEvaluationConfigurationError,
    RagEvaluationIdempotencyConflictError,
    RagEvaluationNotFoundError,
    RagEvaluationQueryService,
    RagEvaluationService,
)

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
ERROR_RESPONSES = {
    status.HTTP_404_NOT_FOUND: {"model": ApiErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ApiErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
}


def _service(session_factory: SessionFactory) -> RagEvaluationService:
    settings = get_settings()
    return RagEvaluationService(
        session_factory=session_factory,
        evaluation_set_path=settings.rag_evaluation_set_path,
        provider=settings.knowledge_provider,
        dataset_id=settings.dify_evaluation_dataset_id,
        build_revision=settings.build_revision,
        retrieval_timeout_seconds=settings.knowledge_retrieval_timeout_seconds,
    )


@router.post(
    "/runs",
    response_model=RagEvaluationAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_200_OK: {
            "model": RagEvaluationAcceptedResponse,
            "description": "Idempotent replay; the existing run is returned.",
        },
        **ERROR_RESPONSES,
    },
)
async def create_rag_evaluation_run(
    command: RagEvaluationRunCreate,
    response: Response,
    session_factory: SessionFactory,
) -> RagEvaluationAcceptedResponse:
    try:
        prepared = await _service(session_factory).prepare_run(
            idempotency_key=command.idempotency_key,
            split=command.split,
            top_k=command.top_k,
            score_threshold=command.score_threshold,
        )
    except RagEvaluationIdempotencyConflictError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="rag_evaluation_idempotency_conflict",
            message="idempotency key already belongs to a different evaluation run",
        ) from exc
    except RagEvaluationConfigurationError as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="rag_evaluation_unavailable",
            message="RAG evaluation is temporarily unavailable",
        ) from exc
    response.status_code = (
        status.HTTP_202_ACCEPTED if prepared.delivery_created else status.HTTP_200_OK
    )
    response.headers["Location"] = f"/api/v1/rag/evaluations/runs/{prepared.run.id}"
    response.headers["X-Idempotent-Replay"] = str(not prepared.delivery_created).lower()
    return RagEvaluationAcceptedResponse(
        run=RagEvaluationRunResponse.model_validate(prepared.run),
        dispatched=prepared.delivery_created,
    )


@router.get("/runs", response_model=RagEvaluationRunListResponse)
async def list_rag_evaluation_runs(
    session: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RagEvaluationRunListResponse:
    result = await RagEvaluationQueryService(session).list(page=page, page_size=page_size)
    return RagEvaluationRunListResponse(
        items=[RagEvaluationRunResponse.model_validate(item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        pages=result.pages,
    )


@router.get(
    "/runs/{run_id}",
    response_model=RagEvaluationDetailResponse,
    responses=ERROR_RESPONSES,
)
async def get_rag_evaluation_run(
    run_id: UUID,
    session: DatabaseSession,
) -> RagEvaluationDetailResponse:
    try:
        run = await RagEvaluationQueryService(session).get(run_id)
    except RagEvaluationNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="rag_evaluation_not_found",
            message="RAG evaluation run does not exist",
            context={"run_id": str(run_id)},
        ) from exc
    return RagEvaluationDetailResponse(
        run=RagEvaluationRunResponse.model_validate(run),
        results=[RagEvaluationResultResponse.model_validate(item) for item in run.results],
    )
