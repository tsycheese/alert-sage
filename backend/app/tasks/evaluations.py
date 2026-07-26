import asyncio
import logging
from contextlib import suppress
from time import monotonic
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.knowledge.factory import create_evaluation_retriever
from app.observability.logging import bind_log_context, correlation_from_celery_headers
from app.services.rag_evaluations import RagEvaluationService
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _execute_evaluation(run_id: UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = redis.lock(
        f"alert-sage:rag-evaluation-lock:{run_id}",
        timeout=settings.rag_evaluation_lock_ttl_seconds,
        blocking=False,
    )
    acquired = False
    try:
        acquired = bool(await lock.acquire())
        if not acquired:
            logger.info(
                "rag_evaluation.task.skipped",
                extra={"operation": "rag_evaluation.run", "status": "lock_skipped"},
            )
            return
        service = RagEvaluationService(
            session_factory=session_factory,
            evaluation_set_path=settings.rag_evaluation_set_path,
            provider=settings.knowledge_provider,
            dataset_id=settings.dify_evaluation_dataset_id,
            build_revision=settings.build_revision,
            retrieval_timeout_seconds=settings.knowledge_retrieval_timeout_seconds,
            query_interval_seconds=settings.rag_evaluation_query_interval_seconds,
        )
        await service.execute(
            run_id,
            retriever=create_evaluation_retriever(settings),
        )
    finally:
        if acquired:
            with suppress(LockError):
                await lock.release()
        await redis.aclose()
        await engine.dispose()


@celery_app.task(
    name="alert_sage.rag_evaluation.run",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_jitter=True,
    retry_kwargs={"max_retries": 2},
)
def run_rag_evaluation(task: Any, rag_evaluation_run_id: str) -> None:
    run_id = UUID(rag_evaluation_run_id)
    started = monotonic()
    with bind_log_context(
        **{
            **correlation_from_celery_headers(getattr(task.request, "headers", None)),
            "rag_evaluation_run_id": run_id,
            "task_id": getattr(task.request, "id", None),
        }
    ):
        status = "failed"
        error_type: str | None = None
        logger.info("rag_evaluation.task.started", extra={"operation": "rag_evaluation.run"})
        try:
            asyncio.run(_execute_evaluation(run_id))
            status = "completed"
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            logger.log(
                logging.ERROR if error_type else logging.INFO,
                "rag_evaluation.task.completed",
                extra={
                    "operation": "rag_evaluation.run",
                    "status": status,
                    "duration_ms": round(max(0.0, monotonic() - started) * 1000, 3),
                    "error_type": error_type,
                },
            )
