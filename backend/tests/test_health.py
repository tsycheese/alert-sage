import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_root_returns_service_metadata() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Alert Sage API",
        "version": "0.1.0",
        "docs": "/docs",
    }


@pytest.mark.asyncio
async def test_liveness_does_not_require_database() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Alert Sage API",
        "version": "0.1.0",
    }
