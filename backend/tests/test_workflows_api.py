from dataclasses import dataclass, field
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.main import app
from app.tasks.dispatcher import get_workflow_dispatcher
from app.workflows.alert.checkpoint import open_alert_workflow_service
from tests.conftest import IsolatedTestDatabase
from tests.test_alerts_api import alert_payload


@dataclass
class FakeWorkflowDispatcher:
    starts: list[UUID] = field(default_factory=list)
    resumes: list[tuple[UUID, UUID]] = field(default_factory=list)
    retries: list[UUID] = field(default_factory=list)
    fail_start: bool = False

    def start(self, workflow_run_id: UUID) -> None:
        if self.fail_start:
            raise RuntimeError("broker unavailable")
        self.starts.append(workflow_run_id)

    def resume(self, workflow_run_id: UUID, decision_id: UUID) -> None:
        self.resumes.append((workflow_run_id, decision_id))

    def retry(self, workflow_run_id: UUID) -> None:
        self.retries.append(workflow_run_id)


@pytest.fixture
def fake_dispatcher() -> FakeWorkflowDispatcher:
    dispatcher = FakeWorkflowDispatcher()
    app.dependency_overrides[get_workflow_dispatcher] = lambda: dispatcher
    try:
        yield dispatcher
    finally:
        app.dependency_overrides.pop(get_workflow_dispatcher, None)


@pytest.mark.asyncio
async def test_workflow_api_runs_to_human_decision(
    api_client: AsyncClient,
    isolated_test_database: IsolatedTestDatabase,
    fake_dispatcher: FakeWorkflowDispatcher,
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
    assert fake_dispatcher.starts == [UUID(started.json()["workflow_run_id"])]

    run_id = fake_dispatcher.starts[0]
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
    assert len(fake_dispatcher.resumes) == 1

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

    workflow_run_id, decision_id = fake_dispatcher.resumes[0]
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
async def test_dispatch_failure_is_persisted_and_retryable(
    api_client: AsyncClient,
    fake_dispatcher: FakeWorkflowDispatcher,
) -> None:
    created = await api_client.post(
        "/api/v1/alerts", json=alert_payload("workflow-api-dispatch-failure")
    )
    alert_id = created.json()["id"]
    fake_dispatcher.fail_start = True

    failed = await api_client.post(
        f"/api/v1/alerts/{alert_id}/workflow",
        json={"idempotency_key": "workflow-api-start-failure-001"},
    )
    assert failed.status_code == 503
    detail = await api_client.get(f"/api/v1/alerts/{alert_id}/workflow")
    assert detail.json()["run"]["status"] == "failed"
    assert detail.json()["run"]["error_code"] == "RuntimeError"

    fake_dispatcher.fail_start = False
    retried = await api_client.post(f"/api/v1/alerts/{alert_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    assert fake_dispatcher.retries == [UUID(retried.json()["workflow_run_id"])]
    detail = await api_client.get(f"/api/v1/alerts/{alert_id}/workflow")
    assert detail.json()["run"]["attempt"] == 2
    assert detail.json()["run"]["error_code"] is None


@pytest.mark.asyncio
async def test_workflow_validation_uses_api_error_envelope(
    api_client: AsyncClient,
    fake_dispatcher: FakeWorkflowDispatcher,
) -> None:
    created = await api_client.post("/api/v1/alerts", json=alert_payload("workflow-api-validation"))
    alert_id = created.json()["id"]

    response = await api_client.post(
        f"/api/v1/alerts/{alert_id}/workflow",
        json={"idempotency_key": "short"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert fake_dispatcher.starts == []
