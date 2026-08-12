from collections import Counter
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.workflows.alert.adapters import default_context_providers
from scripts.bootstrap_demo import DemoBootstrapError, DemoBootstrapper

ALERT_ID = "11111111-1111-4111-8111-111111111111"
WORKFLOW_RUN_ID = "22222222-2222-4222-8222-222222222222"
CASE_ID = "33333333-3333-4333-8333-333333333333"


def response(request: httpx.Request, status_code: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status_code, request=request, json=payload)


@pytest.mark.asyncio
async def test_demo_bootstrap_prepares_waiting_scenario_with_expected_failure() -> None:
    calls: Counter[tuple[str, str]] = Counter()

    async def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        calls[key] += 1
        if key == ("GET", "/api/v1/health/ready"):
            return response(request, 200, {"status": "ok"})
        if key == ("POST", "/api/v1/alerts"):
            return response(request, 201, {"id": ALERT_ID})
        if key == ("POST", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            return response(
                request,
                202,
                {
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "status": "queued",
                    "dispatched": True,
                },
            )
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            return response(
                request,
                200,
                {"run": {"status": "waiting_for_approval"}},
            )
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/events"):
            return response(
                request,
                200,
                {
                    "items": [
                        {
                            "event_type": "tool_failed",
                            "payload": {"tool_name": "logs"},
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {key}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        result = await DemoBootstrapper(
            client,
            web_base_url="http://localhost:15173",
            timeout_seconds=1,
            poll_interval_seconds=0,
        ).run(approve=False, expected_failed_tool="logs")

    assert result.alert_id == ALERT_ID
    assert result.workflow_status == "waiting_for_approval"
    assert result.alert_replayed is False
    assert result.workflow_dispatched is True
    assert result.failed_tool_observed == "logs"
    assert result.case_status is None
    assert calls[("POST", f"/api/v1/alerts/{ALERT_ID}/decisions")] == 0


@pytest.mark.asyncio
async def test_demo_bootstrap_replay_preserves_completed_scenario() -> None:
    calls: Counter[tuple[str, str]] = Counter()

    async def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        calls[key] += 1
        if key == ("GET", "/api/v1/health/ready"):
            return response(request, 200, {"status": "ok"})
        if key == ("POST", "/api/v1/alerts"):
            return response(request, 200, {"id": ALERT_ID})
        if key == ("POST", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            return response(
                request,
                202,
                {
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "status": "completed",
                    "dispatched": False,
                },
            )
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            return response(request, 200, {"run": {"status": "completed"}})
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/events"):
            return response(
                request,
                200,
                {
                    "items": [
                        {
                            "event_type": "tool_failed",
                            "payload": {"tool_name": "logs"},
                        }
                    ]
                },
            )
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/case"):
            return response(
                request,
                200,
                {
                    "id": CASE_ID,
                    "knowledge_sync_status": "synced",
                },
            )
        raise AssertionError(f"unexpected request: {key}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        result = await DemoBootstrapper(
            client,
            web_base_url="http://localhost:15173",
            timeout_seconds=1,
            poll_interval_seconds=0,
        ).run(approve=True, expected_failed_tool="logs")

    assert result.alert_replayed is True
    assert result.workflow_dispatched is False
    assert result.workflow_status == "completed"
    assert result.case_status == "synced"
    assert result.case_id == CASE_ID
    assert calls[("POST", f"/api/v1/alerts/{ALERT_ID}/decisions")] == 0


@pytest.mark.asyncio
async def test_demo_bootstrap_can_approve_and_wait_for_synced_case() -> None:
    workflow_reads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal workflow_reads
        key = (request.method, request.url.path)
        if key == ("GET", "/api/v1/health/ready"):
            return response(request, 200, {"status": "ok"})
        if key == ("POST", "/api/v1/alerts"):
            return response(request, 201, {"id": ALERT_ID})
        if key == ("POST", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            return response(
                request,
                202,
                {
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "status": "queued",
                    "dispatched": True,
                },
            )
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            workflow_reads += 1
            status = "waiting_for_approval" if workflow_reads <= 2 else "completed"
            return response(request, 200, {"run": {"status": status}})
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/events"):
            return response(
                request,
                200,
                {
                    "items": [
                        {
                            "event_type": "tool_failed",
                            "payload": {"tool_name": "logs"},
                        }
                    ]
                },
            )
        if key == ("POST", f"/api/v1/alerts/{ALERT_ID}/decisions"):
            return response(
                request,
                202,
                {
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "status": "queued",
                    "dispatched": True,
                },
            )
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/case"):
            return response(
                request,
                200,
                {"id": CASE_ID, "knowledge_sync_status": "synced"},
            )
        raise AssertionError(f"unexpected request: {key}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        result = await DemoBootstrapper(
            client,
            web_base_url="http://localhost:15173",
            timeout_seconds=1,
            poll_interval_seconds=0,
        ).run(approve=True, expected_failed_tool="logs")

    assert result.workflow_status == "completed"
    assert result.case_status == "synced"
    assert workflow_reads == 3


@pytest.mark.asyncio
async def test_demo_bootstrap_rejects_missing_expected_tool_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key == ("GET", "/api/v1/health/ready"):
            return response(request, 200, {"status": "ok"})
        if key == ("POST", "/api/v1/alerts"):
            return response(request, 201, {"id": ALERT_ID})
        if key == ("POST", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            return response(
                request,
                202,
                {
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "status": "queued",
                    "dispatched": True,
                },
            )
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/workflow"):
            return response(request, 200, {"run": {"status": "waiting_for_approval"}})
        if key == ("GET", f"/api/v1/alerts/{ALERT_ID}/events"):
            return response(request, 200, {"items": []})
        raise AssertionError(f"unexpected request: {key}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        with pytest.raises(DemoBootstrapError, match="was not observed"):
            await DemoBootstrapper(
                client,
                web_base_url="http://localhost:15173",
                timeout_seconds=1,
                poll_interval_seconds=0,
            ).run(approve=False, expected_failed_tool="logs")


@pytest.mark.asyncio
async def test_configured_demo_failure_only_replaces_selected_provider() -> None:
    providers = default_context_providers(failure_provider="logs")
    results: dict[str, str] = {}
    for provider in providers:
        try:
            await provider.collect({"service": "checkout-service"})
        except TimeoutError:
            results[provider.name] = "failed"
        else:
            results[provider.name] = "succeeded"

    assert results == {
        "metrics": "succeeded",
        "logs": "failed",
        "cmdb": "succeeded",
        "knowledge": "succeeded",
    }


def test_demo_failure_configuration_is_restricted_to_offline_development() -> None:
    allowed = Settings(
        _env_file=None,
        runtime_profile="demo",
        component_role="test",
        environment="development",
        knowledge_provider="mock",
        diagnostic_model_provider="mock",
        demo_tool_failure_provider="logs",
    )
    assert allowed.demo_tool_failure_provider == "logs"

    with pytest.raises(ValidationError, match="only allowed in the demo profile"):
        Settings(
            _env_file=None,
            runtime_profile="test",
            component_role="test",
            knowledge_provider="mock",
            diagnostic_model_provider="mock",
            demo_tool_failure_provider="logs",
        )
