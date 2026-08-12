from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.enums import OutboxStatus, OutboxTopic
from app.models.outbox import OutboxMessage
from app.models.workflow import WorkflowRun
from app.services import workflow_commands as workflow_command_module
from app.services.outbox import (
    OutboxEnqueueConflictError,
    OutboxRelayService,
    enqueue_outbox_message,
)
from app.tasks import outbox as outbox_tasks
from app.workflows.alert.service import AlertWorkflowService
from tests.conftest import IsolatedTestDatabase
from tests.test_alert_workflow_runtime import create_runtime_alert


class RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.message_ids: list[UUID] = []

    def publish(self, message: OutboxMessage) -> None:
        self.message_ids.append(message.id)
        if self.fail:
            raise ConnectionError("broker unavailable")


def relay(
    database: IsolatedTestDatabase,
    publisher: RecordingPublisher,
    *,
    batch_size: int = 50,
) -> OutboxRelayService:
    return OutboxRelayService(
        session_factory=database.session_factory,
        publisher=publisher,
        batch_size=batch_size,
        retry_base_seconds=2,
        retry_max_seconds=60,
    )


async def create_message(
    database: IsolatedTestDatabase,
    *,
    suffix: str,
) -> UUID:
    workflow_run_id = uuid4()
    async with database.session_factory() as session:
        message, _created = await enqueue_outbox_message(
            session,
            topic=OutboxTopic.WORKFLOW_START,
            aggregate_id=workflow_run_id,
            idempotency_key=f"workflow:start:{suffix}",
            payload={"workflow_run_id": str(workflow_run_id)},
            correlation={
                "request_id": "10000000-0000-0000-0000-000000000001",
                "outbox_message_id": "20000000-0000-0000-0000-000000000002",
            },
        )
        await session.commit()
        return message.id


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_rejects_conflicting_content(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    run_id = uuid4()
    async with isolated_test_database.session_factory() as session:
        first, created = await enqueue_outbox_message(
            session,
            topic=OutboxTopic.WORKFLOW_START,
            aggregate_id=run_id,
            idempotency_key="workflow:start:stable-key",
            payload={"workflow_run_id": str(run_id)},
        )
        replay, replay_created = await enqueue_outbox_message(
            session,
            topic=OutboxTopic.WORKFLOW_START,
            aggregate_id=run_id,
            idempotency_key="workflow:start:stable-key",
            payload={"workflow_run_id": str(run_id)},
        )
        with pytest.raises(OutboxEnqueueConflictError):
            await enqueue_outbox_message(
                session,
                topic=OutboxTopic.WORKFLOW_RETRY,
                aggregate_id=run_id,
                idempotency_key="workflow:start:stable-key",
                payload={"workflow_run_id": str(run_id)},
            )
        await session.commit()

    assert created is True
    assert replay_created is False
    assert replay.id == first.id


@pytest.mark.asyncio
async def test_outbox_row_rolls_back_with_surrounding_transaction(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    run_id = uuid4()
    async with isolated_test_database.session_factory() as session:
        transaction = await session.begin()
        message, _created = await enqueue_outbox_message(
            session,
            topic=OutboxTopic.WORKFLOW_START,
            aggregate_id=run_id,
            idempotency_key="workflow:start:rolled-back",
            payload={"workflow_run_id": str(run_id)},
        )
        message_id = message.id
        await transaction.rollback()

    async with isolated_test_database.session_factory() as session:
        assert await session.get(OutboxMessage, message_id) is None


@pytest.mark.asyncio
async def test_workflow_and_delivery_intent_commit_atomically(
    isolated_test_database: IsolatedTestDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alert = await create_runtime_alert(
        isolated_test_database.session_factory,
        "outbox-atomicity",
    )

    async def reject_enqueue(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("simulated outbox write failure")

    monkeypatch.setattr(
        workflow_command_module,
        "enqueue_outbox_message",
        reject_enqueue,
    )
    service = AlertWorkflowService(
        session_factory=isolated_test_database.session_factory,
        graph=None,
    )

    with pytest.raises(RuntimeError, match="simulated outbox write failure"):
        await service.prepare_start(
            alert_id=alert.id,
            idempotency_key="outbox-atomic-start",
        )

    async with isolated_test_database.session_factory() as session:
        run_count = await session.scalar(select(func.count()).select_from(WorkflowRun))
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxMessage))
    assert run_count == 0
    assert outbox_count == 0


@pytest.mark.asyncio
async def test_relay_defers_broker_failure_and_recovers_later(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    message_id = await create_message(isolated_test_database, suffix="broker-recovery")
    publisher = RecordingPublisher(fail=True)

    failed = await relay(isolated_test_database, publisher).publish_batch()

    assert failed.selected == 1
    assert failed.published == 0
    assert failed.deferred == 1
    async with isolated_test_database.session_factory() as session:
        message = await session.get(OutboxMessage, message_id)
        assert message is not None
        assert message.status == OutboxStatus.PENDING
        assert message.attempts == 1
        assert message.last_error_type == "ConnectionError"
        assert message.available_at > datetime.now(UTC)
        message.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    publisher.fail = False
    recovered = await relay(isolated_test_database, publisher).publish_batch()

    assert recovered.published == 1
    assert publisher.message_ids == [message_id, message_id]
    async with isolated_test_database.session_factory() as session:
        message = await session.get(OutboxMessage, message_id)
        assert message is not None
        assert message.status == OutboxStatus.PUBLISHED
        assert message.attempts == 2
        assert message.published_at is not None
        assert message.last_error_type is None


@pytest.mark.asyncio
async def test_crash_after_broker_acceptance_is_safe_to_redeliver(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    message_id = await create_message(isolated_test_database, suffix="commit-uncertainty")
    publisher = RecordingPublisher()

    async with isolated_test_database.session_factory() as session:
        transaction = await session.begin()
        message = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.id == message_id).with_for_update()
        )
        assert message is not None
        publisher.publish(message)
        await transaction.rollback()

    result = await relay(isolated_test_database, publisher).publish_batch()

    assert result.published == 1
    assert publisher.message_ids == [message_id, message_id]


@pytest.mark.asyncio
async def test_relay_respects_batch_size(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    ids = [
        await create_message(isolated_test_database, suffix=f"batch-{index}") for index in range(3)
    ]
    publisher = RecordingPublisher()

    first = await relay(isolated_test_database, publisher, batch_size=2).publish_batch()
    second = await relay(isolated_test_database, publisher, batch_size=2).publish_batch()

    assert first.selected == 2
    assert second.selected == 1
    assert sorted(publisher.message_ids) == sorted(ids)


@pytest.mark.asyncio
async def test_skip_locked_prevents_concurrent_relays_from_claiming_same_row(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    ids = {
        await create_message(isolated_test_database, suffix=f"lock-{index}") for index in range(2)
    }
    first_session = isolated_test_database.session_factory()
    second_session = isolated_test_database.session_factory()
    first_transaction = await first_session.begin()
    second_transaction = await second_session.begin()
    try:
        first = await first_session.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.status == OutboxStatus.PENDING)
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        second = await second_session.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.status == OutboxStatus.PENDING)
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        assert first is not None
        assert second is not None
        assert {first.id, second.id} == ids
    finally:
        await second_transaction.rollback()
        await first_transaction.rollback()
        await second_session.close()
        await first_session.close()


def test_periodic_outbox_task_runs_one_relay_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def completed() -> object:
        return type("Result", (), {"selected": 3})()

    monkeypatch.setattr(outbox_tasks, "_publish_outbox", completed)

    outbox_tasks.run_outbox_publish.run()
