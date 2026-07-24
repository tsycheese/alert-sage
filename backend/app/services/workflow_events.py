from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import WorkflowEventType
from app.models.workflow import WorkflowEvent, WorkflowRun
from app.observability.logging import event_correlation_payload


async def append_workflow_event(
    session: AsyncSession,
    run: WorkflowRun,
    *,
    idempotency_key: str,
    event_type: WorkflowEventType,
    payload: dict[str, object],
    node_name: str | None = None,
    status: object | None = None,
) -> WorkflowEvent:
    existing = await session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == run.id,
            WorkflowEvent.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    sequence = (
        int(
            await session.scalar(
                select(func.coalesce(func.max(WorkflowEvent.sequence), 0)).where(
                    WorkflowEvent.workflow_run_id == run.id
                )
            )
            or 0
        )
        + 1
    )
    event_payload = dict(payload)
    correlation = event_correlation_payload()
    if correlation:
        event_payload["correlation"] = correlation
    event = WorkflowEvent(
        workflow_run_id=run.id,
        sequence=sequence,
        idempotency_key=idempotency_key,
        event_type=event_type,
        node_name=node_name,
        status=str(status) if status is not None else None,
        payload=event_payload,
    )
    session.add(event)
    await session.flush()
    return event
