from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.integrations.knowledge.cases import CaseDocument
from app.models.case import Case
from app.models.enums import (
    HumanDecisionAction,
    KnowledgeSyncStatus,
    OutboxStatus,
    OutboxTopic,
    WorkflowEventType,
)
from app.models.outbox import OutboxMessage
from app.models.workflow import WorkflowEvent
from app.services.cases import CaseSyncExecutionError, CaseSyncService
from app.workflows.alert.checkpoint import open_alert_workflow_service
from tests.conftest import IsolatedTestDatabase
from tests.test_alert_workflow_runtime import create_runtime_alert, decision


class RecordingPublisher:
    def __init__(self) -> None:
        self.documents: list[CaseDocument] = []

    async def publish(self, document: CaseDocument, *, idempotency_key: str) -> str:
        self.documents.append(document)
        assert idempotency_key == f"case-sync:{document.case_id}"
        return f"knowledge-document-{document.case_id}"


class FlakyPublisher(RecordingPublisher):
    async def publish(self, document: CaseDocument, *, idempotency_key: str) -> str:
        await super().publish(document, idempotency_key=idempotency_key)
        if len(self.documents) == 1:
            raise TimeoutError("mock knowledge service timeout")
        return f"knowledge-document-{document.case_id}"


async def create_approved_case(database: IsolatedTestDatabase, suffix: str) -> tuple[UUID, UUID]:
    alert = await create_runtime_alert(database.session_factory, f"case-{suffix}")
    async with open_alert_workflow_service(
        session_factory=database.session_factory,
        database_url=database.checkpoint_url,
    ) as service:
        waiting = await service.start(
            alert_id=alert.id,
            idempotency_key=f"case-start-{suffix}",
        )
        completed = await service.resume(
            workflow_run_id=waiting.workflow_run_id,
            command=decision(HumanDecisionAction.APPROVE, f"case-{suffix}"),
            actor="case-reviewer@example.com",
        )
    assert completed.case_id is not None
    return alert.id, completed.case_id


@pytest.mark.asyncio
async def test_approval_creates_one_structured_case(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    alert_id, case_id = await create_approved_case(isolated_test_database, "creation")

    async with isolated_test_database.session_factory() as session:
        case = await session.get(Case, case_id)
        case_count = await session.scalar(select(func.count()).select_from(Case))
        created_events = await session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.event_type == WorkflowEventType.CASE_CREATED)
        )

    assert alert_id
    assert case is not None
    assert case_count == 1
    assert created_events == 1
    assert case.knowledge_sync_status == KnowledgeSyncStatus.PENDING
    assert case.knowledge_sync_attempt == 0
    assert case.title == "order-service: HighCPUUsage 处置案例"
    assert "order-service" in case.tags
    assert case.root_cause
    assert case.resolution
    assert case.evidence


@pytest.mark.asyncio
async def test_approval_enqueues_case_sync_in_same_transaction(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    alert = await create_runtime_alert(
        isolated_test_database.session_factory,
        "case-auto-dispatch",
    )
    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
    ) as service:
        waiting = await service.start(
            alert_id=alert.id,
            idempotency_key="case-start-auto-dispatch",
        )
        completed = await service.resume(
            workflow_run_id=waiting.workflow_run_id,
            command=decision(HumanDecisionAction.APPROVE, "case-auto-dispatch"),
            actor="case-reviewer@example.com",
        )

    assert completed.case_id is not None
    async with isolated_test_database.session_factory() as session:
        message = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.topic == OutboxTopic.CASE_SYNC,
                OutboxMessage.aggregate_id == completed.case_id,
            )
        )
    assert message is not None
    assert message.status == OutboxStatus.PENDING
    assert message.payload == {"case_id": str(completed.case_id)}


@pytest.mark.asyncio
async def test_case_sync_is_idempotent_and_audited(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    _alert_id, case_id = await create_approved_case(isolated_test_database, "sync")
    publisher = RecordingPublisher()
    service = CaseSyncService(
        session_factory=isolated_test_database.session_factory,
        publisher=publisher,
    )

    first = await service.execute(case_id)
    replay = await service.execute(case_id)

    assert first.status == KnowledgeSyncStatus.SYNCED
    assert replay.status == KnowledgeSyncStatus.SYNCED
    assert first.external_document_id == f"knowledge-document-{case_id}"
    assert len(publisher.documents) == 1
    async with isolated_test_database.session_factory() as session:
        case = await session.get(Case, case_id)
        events = list(
            await session.scalars(
                select(WorkflowEvent).where(
                    WorkflowEvent.event_type.in_(
                        [
                            WorkflowEventType.CASE_SYNC_STARTED,
                            WorkflowEventType.CASE_SYNC_SUCCEEDED,
                        ]
                    )
                )
            )
        )
    assert case is not None
    assert case.knowledge_sync_attempt == 1
    assert case.synced_at is not None
    assert len(events) == 2


@pytest.mark.asyncio
async def test_failed_case_sync_can_be_retried(
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    _alert_id, case_id = await create_approved_case(isolated_test_database, "retry")
    publisher = FlakyPublisher()
    service = CaseSyncService(
        session_factory=isolated_test_database.session_factory,
        publisher=publisher,
    )

    with pytest.raises(CaseSyncExecutionError):
        await service.execute(case_id)
    async with isolated_test_database.session_factory() as session:
        failed = await session.get(Case, case_id)
        assert failed is not None
        assert failed.knowledge_sync_status == KnowledgeSyncStatus.FAILED
        assert failed.sync_error_code == "TimeoutError"

    assert await service.prepare_retry(case_id, enqueue=False) is True
    synced = await service.execute(case_id)

    assert synced.status == KnowledgeSyncStatus.SYNCED
    assert len(publisher.documents) == 2
    async with isolated_test_database.session_factory() as session:
        case = await session.get(Case, case_id)
        failed_events = await session.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.event_type == WorkflowEventType.CASE_SYNC_FAILED)
        )
    assert case is not None
    assert case.knowledge_sync_attempt == 2
    assert case.sync_error_code is None
    assert failed_events == 1


@pytest.mark.asyncio
async def test_case_api_queries_and_dispatches_retry(
    api_client: AsyncClient,
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    alert_id, case_id = await create_approved_case(isolated_test_database, "api")
    fetched = await api_client.get(f"/api/v1/alerts/{alert_id}/case")
    retried = await api_client.post(f"/api/v1/alerts/{alert_id}/case/retry")

    assert fetched.status_code == 200
    assert fetched.json()["id"] == str(case_id)
    assert fetched.json()["knowledge_sync_status"] == "pending"
    assert retried.status_code == 202
    assert retried.json() == {
        "case_id": str(case_id),
        "status": "pending",
        "dispatched": True,
    }
    async with isolated_test_database.session_factory() as session:
        messages = list(
            await session.scalars(
                select(OutboxMessage)
                .where(
                    OutboxMessage.topic == OutboxTopic.CASE_SYNC,
                    OutboxMessage.aggregate_id == case_id,
                )
                .order_by(OutboxMessage.created_at)
            )
        )
    assert len(messages) == 2
    assert messages[-1].idempotency_key.endswith("delivery-2")


@pytest.mark.asyncio
async def test_rejected_workflow_has_no_case(
    api_client: AsyncClient,
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    alert = await create_runtime_alert(isolated_test_database.session_factory, "case-rejected")
    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
    ) as service:
        waiting = await service.start(
            alert_id=alert.id,
            idempotency_key="case-start-rejected",
        )
        rejected = await service.resume(
            workflow_run_id=waiting.workflow_run_id,
            command=decision(HumanDecisionAction.REJECT, "case-rejected"),
            actor="case-reviewer@example.com",
        )

    assert rejected.case_id is None
    response = await api_client.get(f"/api/v1/alerts/{alert.id}/case")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"
