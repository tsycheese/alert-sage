import asyncio
import logging
from time import monotonic

from app.integrations.knowledge.cases import CaseDocument, CasePublisher
from app.integrations.knowledge.retrieval import DocumentChunk, KnowledgeRetriever
from app.observability.metrics import (
    observe_knowledge_operation,
    observe_knowledge_results,
)

logger = logging.getLogger(__name__)


class InstrumentedKnowledgeRetriever:
    def __init__(self, delegate: KnowledgeRetriever, *, provider: str) -> None:
        self.delegate = delegate
        self.provider = provider

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[DocumentChunk]:
        started = monotonic()
        status = "error"
        result_count: int | None = None
        error_type: str | None = None
        try:
            chunks = await self.delegate.retrieve(
                query,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            status = "success"
            result_count = len(chunks)
            observe_knowledge_results(provider=self.provider, count=len(chunks))
            return chunks
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            duration_seconds = max(0.0, monotonic() - started)
            observe_knowledge_operation(
                provider=self.provider,
                operation="retrieve",
                status=status,
                duration_seconds=duration_seconds,
            )
            logger.log(
                logging.ERROR if error_type else logging.INFO,
                "knowledge.operation.completed",
                extra={
                    "provider": self.provider,
                    "operation": "retrieve",
                    "status": status,
                    "result_count": result_count,
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "error_type": error_type,
                },
            )


class InstrumentedCasePublisher:
    def __init__(self, delegate: CasePublisher, *, provider: str) -> None:
        self.delegate = delegate
        self.provider = provider

    async def publish(self, document: CaseDocument, *, idempotency_key: str) -> str:
        started = monotonic()
        status = "error"
        error_type: str | None = None
        try:
            document_id = await self.delegate.publish(
                document,
                idempotency_key=idempotency_key,
            )
            status = "success"
            return document_id
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            duration_seconds = max(0.0, monotonic() - started)
            observe_knowledge_operation(
                provider=self.provider,
                operation="publish",
                status=status,
                duration_seconds=duration_seconds,
            )
            logger.log(
                logging.ERROR if error_type else logging.INFO,
                "knowledge.operation.completed",
                extra={
                    "provider": self.provider,
                    "operation": "publish",
                    "status": status,
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "error_type": error_type,
                },
            )
