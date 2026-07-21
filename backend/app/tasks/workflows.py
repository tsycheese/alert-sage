import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import monotonic
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.knowledge.factory import create_knowledge_retriever
from app.integrations.llm.factory import create_diagnostic_model
from app.observability.metrics import observe_workflow
from app.tasks.celery_app import celery_app
from app.workflows.alert.adapters import default_context_providers
from app.workflows.alert.checkpoint import open_alert_workflow_service
from app.workflows.alert.service import AlertWorkflowService, WorkflowExecutionResult

WorkflowOperation = Callable[[AlertWorkflowService], Awaitable[object]]


async def _run_locked(workflow_run_id: UUID, operation: WorkflowOperation) -> object | None:
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
        acquired = bool(await lock.acquire())
        if not acquired:
            return None
        async with open_alert_workflow_service(
            session_factory=session_factory,
            database_url=settings.database_url,
            context_providers=default_context_providers(
                knowledge_retriever=create_knowledge_retriever(settings)
            ),
            diagnostic_model=create_diagnostic_model(settings),
            tool_timeout_seconds=settings.workflow_tool_timeout_seconds,
        ) as service:
            return await operation(service)
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
        result = await _run_locked(workflow_run_id, operation)
        status = result.status if isinstance(result, WorkflowExecutionResult) else "lock_skipped"
        return result
    finally:
        observe_workflow(
            operation=operation_name,
            status=status,
            duration_seconds=max(0.0, monotonic() - started),
        )


@celery_app.task(name="alert_sage.workflow.start")
def run_workflow_start(workflow_run_id: str) -> None:
    run_id = UUID(workflow_run_id)
    result = asyncio.run(
        _run_observed(
            run_id,
            "start",
            lambda service: service.execute_start(run_id),
        )
    )
    _dispatch_case_sync(result)


@celery_app.task(name="alert_sage.workflow.resume")
def run_workflow_resume(workflow_run_id: str, decision_id: str) -> None:
    run_id = UUID(workflow_run_id)
    stored_decision_id = UUID(decision_id)
    result = asyncio.run(
        _run_observed(
            run_id,
            "resume",
            lambda service: service.execute_resume(
                workflow_run_id=run_id,
                decision_id=stored_decision_id,
            ),
        )
    )
    _dispatch_case_sync(result)


@celery_app.task(name="alert_sage.workflow.retry")
def run_workflow_retry(workflow_run_id: str) -> None:
    run_id = UUID(workflow_run_id)
    result = asyncio.run(
        _run_observed(
            run_id,
            "retry",
            lambda service: service.execute_retry(run_id),
        )
    )
    _dispatch_case_sync(result)


def _dispatch_case_sync(result: object | None) -> None:
    if not isinstance(result, WorkflowExecutionResult) or result.case_id is None:
        return
    from app.tasks.cases import run_case_sync

    run_case_sync.apply_async(
        args=[str(result.case_id)],
        task_id=f"case-sync-{result.case_id}",
    )
