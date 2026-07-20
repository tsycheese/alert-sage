import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.config import get_settings
from app.core.errors import ApiError
from app.integrations.knowledge.dify import KnowledgeProviderError
from app.integrations.knowledge.factory import get_knowledge_retriever
from app.integrations.knowledge.retrieval import KnowledgeRetriever
from app.schemas.error import ApiErrorResponse
from app.schemas.knowledge import (
    KnowledgeChunkResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)

router = APIRouter()
Retriever = Annotated[KnowledgeRetriever, Depends(get_knowledge_retriever)]


@router.post(
    "/search",
    response_model=KnowledgeSearchResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ApiErrorResponse},
    },
)
async def search_knowledge(
    command: KnowledgeSearchRequest,
    retriever: Retriever,
) -> KnowledgeSearchResponse:
    settings = get_settings()
    try:
        chunks = await asyncio.wait_for(
            retriever.retrieve(
                command.query.strip(),
                top_k=command.top_k,
                score_threshold=command.score_threshold,
            ),
            timeout=settings.knowledge_retrieval_timeout_seconds,
        )
    except (TimeoutError, KnowledgeProviderError) as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="knowledge_provider_unavailable",
            message="knowledge retrieval is temporarily unavailable",
            context={"provider": settings.knowledge_provider},
        ) from exc
    return KnowledgeSearchResponse(
        query=command.query.strip(),
        provider=settings.knowledge_provider,
        items=[
            KnowledgeChunkResponse.model_validate(chunk, from_attributes=True) for chunk in chunks
        ],
    )
