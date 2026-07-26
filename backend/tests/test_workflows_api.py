from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.enums import OutboxStatus, OutboxTopic
from app.models.outbox import OutboxMessage
from app.workflows.alert.checkpoint import open_alert_workflow_service
from tests.conftest import IsolatedTestDatabase
from tests.test_alerts_api import alert_payload


@pytest.mark.asyncio
async def test_workflow_api_runs_to_human_decision(
    api_client: AsyncClient,
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    created = await api_client.post("/api/v1/alerts", json=alert_payload("workflow-api-happy-path"))
    alert_id = created.json()["id"]

    started = await api_client.post(
        f"/api/v1/alerts/{alert_id}/workflow",
        json={"idempotency_key": "workflow-api-start-001"},
    )
    replayed = await api_client.post(
        f"/api/v1/alerts/{alert_id}/workflow",
        json={"idempotency_key": "workflow-api-start-001"},
    )

    assert started.status_code == 202
    assert started.json()["status"] == "queued"
    assert replayed.json()["dispatched"] is False
    run_id = UUID(started.json()["workflow_run_id"])
    async with isolated_test_database.session_factory() as session:
        start_messages = list(
            await session.scalars(
                select(OutboxMessage).where(OutboxMessage.topic == OutboxTopic.WORKFLOW_START)
            )
        )
    assert len(start_messages) == 1
    assert start_messages[0].aggregate_id == run_id
    assert start_messages[0].payload == {"workflow_run_id": str(run_id)}
    assert start_messages[0].status == OutboxStatus.PENDING
    assert start_messages[0].correlation["request_id"] == started.headers["X-Request-ID"]

    queued_events = await api_client.get(f"/api/v1/alerts/{alert_id}/events")
    assert queued_events.status_code == 200
    assert (
        queued_events.json()["items"][0]["payload"]["correlation"]["request_id"]
        == (started.headers["X-Request-ID"])
    )

    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
    ) as service:
        waiting = await service.execute_start(run_id)
    assert waiting.status == "waiting_for_approval"

    detail = await api_client.get(f"/api/v1/alerts/{alert_id}/workflow")
    events = await api_client.get(f"/api/v1/alerts/{alert_id}/events")
    assert detail.status_code == 200
    assert detail.json()["report"]["summary"]
    assert events.json()["last_sequence"] >= 10
    assert events.json()["items"][-1]["event_type"] == "human_input_required"

    decision = await api_client.post(
        f"/api/v1/alerts/{alert_id}/decisions",
        json={
            "idempotency_key": "workflow-api-decision-001",
            "action": "approve",
            "actor": "reviewer@example.com",
        },
    )
    assert decision.status_code == 202
    assert decision.json()["dispatched"] is True
    replayed_decision = await api_client.post(
        f"/api/v1/alerts/{alert_id}/decisions",
        json={
            "idempotency_key": "workflow-api-decision-001",
            "action": "approve",
            "actor": "reviewer@example.com",
        },
    )
    assert replayed_decision.status_code == 202
    assert replayed_decision.json()["dispatched"] is False
    async with isolated_test_database.session_factory() as session:
        resume_message = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.topic == OutboxTopic.WORKFLOW_RESUME)
        )
    assert resume_message is not None
    assert resume_message.aggregate_id == run_id

    conflicting_decision = await api_client.post(
        f"/api/v1/alerts/{alert_id}/decisions",
        json={
            "idempotency_key": "workflow-api-decision-002",
            "action": "reject",
            "actor": "another-reviewer@example.com",
        },
    )
    assert conflicting_decision.status_code == 409
    assert conflicting_decision.json()["error"]["code"] == "workflow_state_conflict"

    workflow_run_id = UUID(resume_message.payload["workflow_run_id"])
    decision_id = UUID(resume_message.payload["decision_id"])
    async with open_alert_workflow_service(
        session_factory=isolated_test_database.session_factory,
        database_url=isolated_test_database.checkpoint_url,
    ) as service:
        completed = await service.execute_resume(
            workflow_run_id=workflow_run_id,
            decision_id=decision_id,
        )
    assert completed.status == "completed"
    assert (await api_client.get(f"/api/v1/alerts/{alert_id}/workflow")).json()["run"][
        "status"
    ] == "completed"


@pytest.mark.asyncio
async def test_workflow_delivery_intent_is_committed_without_contacting_broker(
    api_client: AsyncClient,
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    created = await api_client.post(
        "/api/v1/alerts", json=alert_payload("workflow-api-dispatch-failure")
    )
    alert_id = created.json()["id"]
    accepted = await api_client.post(
        f"/api/v1/alerts/{alert_id}/workflow",
        json={"idempotency_key": "workflow-api-start-failure-001"},
    )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"
    detail = await api_client.get(f"/api/v1/alerts/{alert_id}/workflow")
    assert detail.json()["run"]["status"] == "queued"
    assert detail.json()["run"]["error_code"] is None
    async with isolated_test_database.session_factory() as session:
        pending = await session.scalar(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.status == OutboxStatus.PENDING)
        )
    assert pending == 1


@pytest.mark.asyncio
async def test_workflow_validation_uses_api_error_envelope(
    api_client: AsyncClient,
    isolated_test_database: IsolatedTestDatabase,
) -> None:
    created = await api_client.post("/api/v1/alerts", json=alert_payload("workflow-api-validation"))
    alert_id = created.json()["id"]

    response = await api_client.post(
        f"/api/v1/alerts/{alert_id}/workflow",
        json={"idempotency_key": "short"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    async with isolated_test_database.session_factory() as session:
        message_count = await session.scalar(select(func.count()).select_from(OutboxMessage))
    assert message_count == 0
