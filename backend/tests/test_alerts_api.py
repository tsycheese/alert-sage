import asyncio
from copy import deepcopy
from datetime import datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient


def alert_payload(external_alert_id: str = "alert-20260718-001") -> dict[str, object]:
    return {
        "source": "web",
        "external_alert_id": external_alert_id,
        "alert_name": "HighCPUUsage",
        "service": "order-service",
        "instance": "order-service-01",
        "severity": "critical",
        "value": 92.5,
        "threshold": 80,
        "started_at": "2026-07-18T14:30:00+08:00",
        "payload": {"summary": "CPU usage remained high for five minutes"},
    }


@pytest.mark.asyncio
async def test_create_get_and_replay_alert(api_client: AsyncClient) -> None:
    payload = alert_payload()

    created = await api_client.post("/api/v1/alerts", json=payload)

    assert created.status_code == 201
    assert created.headers["x-idempotent-replay"] == "false"
    body = created.json()
    assert created.headers["location"] == f"/api/v1/alerts/{body['id']}"
    assert body["status"] == "received"
    assert body["payload"] == {
        "summary": "CPU usage remained high for five minutes",
        "value": 92.5,
        "threshold": 80,
    }
    assert len(body["fingerprint"]) == 64

    fetched = await api_client.get(f"/api/v1/alerts/{body['id']}")
    replayed = await api_client.post("/api/v1/alerts", json=payload)
    listed = await api_client.get("/api/v1/alerts")

    assert fetched.status_code == 200
    assert fetched.json() == body
    assert replayed.status_code == 200
    assert replayed.headers["x-idempotent-replay"] == "true"
    assert replayed.json()["id"] == body["id"]
    assert listed.json()["total"] == 1


@pytest.mark.asyncio
async def test_conflicting_idempotency_key_returns_409(api_client: AsyncClient) -> None:
    original = alert_payload()
    conflicting = deepcopy(original)
    conflicting["value"] = 97.0

    assert (await api_client.post("/api/v1/alerts", json=original)).status_code == 201
    response = await api_client.post("/api/v1/alerts", json=conflicting)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "alert_idempotency_conflict"
    assert (await api_client.get("/api/v1/alerts")).json()["total"] == 1


@pytest.mark.asyncio
async def test_concurrent_replays_create_one_alert(api_client: AsyncClient) -> None:
    payload = alert_payload("concurrent-alert")

    first, second = await asyncio.gather(
        api_client.post("/api/v1/alerts", json=payload),
        api_client.post("/api/v1/alerts", json=payload),
    )

    assert sorted((first.status_code, second.status_code)) == [200, 201]
    assert first.json()["id"] == second.json()["id"]
    assert (await api_client.get("/api/v1/alerts")).json()["total"] == 1


@pytest.mark.asyncio
async def test_list_filters_and_paginates_alerts(api_client: AsyncClient) -> None:
    first = alert_payload("critical-order-1")
    second = alert_payload("critical-order-2")
    third = alert_payload("warning-payment")
    third["service"] = "payment-service"
    third["severity"] = "warning"

    for payload in (first, second, third):
        assert (await api_client.post("/api/v1/alerts", json=payload)).status_code == 201

    response = await api_client.get(
        "/api/v1/alerts",
        params={
            "status": "received",
            "severity": "critical",
            "service": "order-service",
            "page": 1,
            "page_size": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert body["pages"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["service"] == "order-service"
    assert body["items"][0]["severity"] == "critical"

    all_matching = await api_client.get(
        "/api/v1/alerts",
        params={"severity": "critical", "service": "order-service", "page_size": 10},
    )
    created_times = [
        datetime.fromisoformat(item["created_at"]) for item in all_matching.json()["items"]
    ]
    assert created_times == sorted(created_times, reverse=True)


@pytest.mark.asyncio
async def test_invalid_pagination_returns_422(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/alerts", params={"page": 0, "page_size": 101})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_missing_alert_returns_404(api_client: AsyncClient) -> None:
    response = await api_client.get(f"/api/v1/alerts/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "alert_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("severity", "emergency"),
        ("started_at", "2026-07-18T14:30:00"),
    ],
)
async def test_invalid_alert_returns_422(
    api_client: AsyncClient,
    field: str,
    invalid_value: str,
) -> None:
    payload = alert_payload(f"invalid-{field}")
    payload[field] = invalid_value

    response = await api_client.post("/api/v1/alerts", json=payload)

    assert response.status_code == 422
