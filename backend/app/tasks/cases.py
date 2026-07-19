import asyncio
from contextlib import suppress
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.integrations.knowledge.cases import MockCasePublisher
from app.services.cases import CaseSyncService
from app.tasks.celery_app import celery_app


async def _sync_case(case_id: UUID) -> None:
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
        acquired = bool(await lock.acquire())
        if not acquired:
            return
        service = CaseSyncService(
            session_factory=session_factory,
            publisher=MockCasePublisher(),
            timeout_seconds=settings.case_sync_timeout_seconds,
        )
        result = await service.execute(case_id)
        workflow_run_id = result.workflow_run_id
    finally:
        if workflow_run_id is None:
            with suppress(Exception):
                result = await CaseSyncService(
                    session_factory=session_factory,
                    publisher=MockCasePublisher(),
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


@celery_app.task(name="alert_sage.case.sync")
def run_case_sync(case_id: str) -> None:
    asyncio.run(_sync_case(UUID(case_id)))
