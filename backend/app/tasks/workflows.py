import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import monotonic
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.knowledge.factory import create_knowledge_retriever
from app.integrations.llm.factory import create_diagnostic_model
from app.models.workflow import WorkflowRun
from app.observability.logging import bind_log_context, correlation_from_celery_headers
from app.observability.metrics import observe_workflow
from app.tasks.celery_app import celery_app
from app.workflows.alert.adapters import default_context_providers
from app.workflows.alert.checkpoint import open_alert_workflow_service
from app.workflows.alert.service import AlertWorkflowService, WorkflowExecutionResult

WorkflowOperation = Callable[[AlertWorkflowService], Awaitable[object]]
logger = logging.getLogger(__name__)


async def _run_locked(
    workflow_run_id: UUID,
    operation_name: str,
    operation: WorkflowOperation,
) -> object | None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = redis.lock(
        f"alert-sage:workflow-lock:{workflow_run_id}",
        timeout=settings.workflow_lock_ttl_seconds,
        blocking=False,
    )
    acquired = False
    try:
        async with session_factory() as correlation_session:
            run = await correlation_session.get(WorkflowRun, workflow_run_id)
        correlation = {
            "workflow_run_id": workflow_run_id,
            "alert_id": run.alert_id if run is not None else None,
            "thread_id": run.thread_id if run is not None else None,
        }
        with bind_log_context(**correlation):
            started = monotonic()
            status: object = "failed"
            error_type: str | None = None
            logger.info("workflow.task.started", extra={"operation": operation_name})
            try:
                acquired = bool(await lock.acquire())
                if not acquired:
                    status = "lock_skipped"
                    return None
                async with open_alert_workflow_service(
                    session_factory=session_factory,
                    database_url=settings.database_url,
                    context_providers=default_context_providers(
                        knowledge_retriever=create_knowledge_retriever(settings),
                        failure_provider=settings.demo_tool_failure_provider,
                    ),
                    diagnostic_model=create_diagnostic_model(settings),
                    tool_timeout_seconds=settings.workflow_tool_timeout_seconds,
                ) as service:
                    result = await operation(service)
                    status = (
                        result.status
                        if isinstance(result, WorkflowExecutionResult)
                        else "completed"
                    )
                    return result
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                logger.log(
                    logging.ERROR if error_type else logging.INFO,
                    "workflow.task.completed",
                    extra={
                        "operation": operation_name,
                        "status": str(getattr(status, "value", status)),
                        "duration_ms": round(max(0.0, monotonic() - started) * 1000, 3),
                        "error_type": error_type,
                    },
                )
    finally:
        with suppress(Exception):
            await redis.publish(f"alert-sage:workflow:{workflow_run_id}", "changed")
        if acquired:
            with suppress(LockError):
                await lock.release()
        await redis.aclose()
        await engine.dispose()


async def _run_observed(
    workflow_run_id: UUID,
    operation_name: str,
    operation: WorkflowOperation,
) -> object | None:
    started = monotonic()
    status: object = "failed"
    try:
        result = await _run_locked(workflow_run_id, operation_name, operation)
        status = result.status if isinstance(result, WorkflowExecutionResult) else "lock_skipped"
        return result
    finally:
        observe_workflow(
            operation=operation_name,
            status=status,
            duration_seconds=max(0.0, monotonic() - started),
        )


@celery_app.task(name="alert_sage.workflow.start", bind=True)
def run_workflow_start(task: Any, workflow_run_id: str) -> None:
    run_id = UUID(workflow_run_id)
    with bind_log_context(
        **{
            **correlation_from_celery_headers(getattr(task.request, "headers", None)),
            "workflow_run_id": run_id,
            "task_id": getattr(task.request, "id", None),
        }
    ):
        asyncio.run(
            _run_observed(
                run_id,
                "start",
                lambda service: service.execute_start(run_id),
            )
        )


@celery_app.task(name="alert_sage.workflow.resume", bind=True)
def run_workflow_resume(task: Any, workflow_run_id: str, decision_id: str) -> None:
    run_id = UUID(workflow_run_id)
    stored_decision_id = UUID(decision_id)
    with bind_log_context(
        **{
            **correlation_from_celery_headers(getattr(task.request, "headers", None)),
            "workflow_run_id": run_id,
            "task_id": getattr(task.request, "id", None),
        }
    ):
        asyncio.run(
            _run_observed(
                run_id,
                "resume",
                lambda service: service.execute_resume(
                    workflow_run_id=run_id,
                    decision_id=stored_decision_id,
                ),
            )
        )


@celery_app.task(name="alert_sage.workflow.retry", bind=True)
def run_workflow_retry(task: Any, workflow_run_id: str) -> None:
    run_id = UUID(workflow_run_id)
    with bind_log_context(
        **{
            **correlation_from_celery_headers(getattr(task.request, "headers", None)),
            "workflow_run_id": run_id,
            "task_id": getattr(task.request, "id", None),
        }
    ):
        asyncio.run(
            _run_observed(
                run_id,
                "retry",
                lambda service: service.execute_retry(run_id),
            )
        )
