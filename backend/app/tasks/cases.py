import asyncio
import logging
from contextlib import suppress
from time import monotonic
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.knowledge.factory import create_case_publisher
from app.models.case import Case
from app.models.diagnosis import DiagnosisReport
from app.models.workflow import WorkflowRun
from app.observability.logging import bind_log_context, correlation_from_celery_headers
from app.observability.metrics import label_value, observe_case_sync
from app.services.cases import CaseSyncResult, CaseSyncService
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _sync_case(case_id: UUID) -> CaseSyncResult | None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = redis.lock(
        f"alert-sage:case-sync-lock:{case_id}",
        timeout=settings.workflow_lock_ttl_seconds,
        blocking=False,
    )
    acquired = False
    workflow_run_id: UUID | None = None
    try:
        async with session_factory() as correlation_session:
            row = (
                await correlation_session.execute(
                    select(WorkflowRun.id, WorkflowRun.alert_id, WorkflowRun.thread_id)
                    .select_from(Case)
                    .join(
                        DiagnosisReport,
                        Case.diagnosis_report_id == DiagnosisReport.id,
                    )
                    .join(WorkflowRun, DiagnosisReport.workflow_run_id == WorkflowRun.id)
                    .where(Case.id == case_id)
                )
            ).one_or_none()
        correlation = {
            "case_id": case_id,
            "workflow_run_id": row.id if row is not None else None,
            "alert_id": row.alert_id if row is not None else None,
            "thread_id": row.thread_id if row is not None else None,
        }
        with bind_log_context(**correlation):
            started = monotonic()
            status = "failed"
            error_type: str | None = None
            logger.info("case_sync.task.started", extra={"operation": "case_sync"})
            try:
                acquired = bool(await lock.acquire())
                if not acquired:
                    status = "lock_skipped"
                    return None
                service = CaseSyncService(
                    session_factory=session_factory,
                    publisher=create_case_publisher(settings),
                    timeout_seconds=settings.case_sync_timeout_seconds,
                )
                result = await service.execute(case_id)
                workflow_run_id = result.workflow_run_id
                status = label_value(result.status)
                return result
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                logger.log(
                    logging.ERROR if error_type else logging.INFO,
                    "case_sync.task.completed",
                    extra={
                        "operation": "case_sync",
                        "status": status,
                        "duration_ms": round(max(0.0, monotonic() - started) * 1000, 3),
                        "error_type": error_type,
                    },
                )
    finally:
        if workflow_run_id is None:
            with suppress(Exception):
                result = await CaseSyncService(
                    session_factory=session_factory,
                    publisher=create_case_publisher(settings),
                ).get_result(case_id)
                workflow_run_id = result.workflow_run_id
        if workflow_run_id is not None:
            with suppress(Exception):
                await redis.publish(f"alert-sage:workflow:{workflow_run_id}", "changed")
        if acquired:
            with suppress(LockError):
                await lock.release()
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="alert_sage.case.sync", bind=True)
def run_case_sync(task: Any, case_id: str) -> None:
    settings = get_settings()
    started = monotonic()
    status = "failed"
    stored_case_id = UUID(case_id)
    with bind_log_context(
        **{
            **correlation_from_celery_headers(getattr(task.request, "headers", None)),
            "case_id": stored_case_id,
            "task_id": getattr(task.request, "id", None),
        }
    ):
        try:
            result = asyncio.run(_sync_case(stored_case_id))
            status = label_value(result.status) if result is not None else "lock_skipped"
        finally:
            observe_case_sync(
                provider=settings.knowledge_provider,
                status=status,
                duration_seconds=max(0.0, monotonic() - started),
            )
