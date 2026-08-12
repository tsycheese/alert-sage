import json
from collections.abc import Callable

import httpx
import pytest

from app.core.config import Settings
from app.integrations.feishu.client import FeishuApiError, FeishuClient

DATASET_ID = "8dc8a66d-8202-4099-b0ee-6d42e0bf57d1"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls = 0
        self.delete_calls = 0

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        assert ex == 6900
        self.values[key] = value
        self.set_calls += 1

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.delete_calls += 1


def worker_settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_profile="real",
        component_role="worker",
        knowledge_provider="dify",
        dify_api_key="dify-secret",
        dify_dataset_id=DATASET_ID,
        diagnostic_model_provider="deepseek",
        diagnostic_model_api_key="deepseek-secret",
        feishu_enabled=True,
        feishu_app_id="cli_test",
        feishu_app_secret="app-secret",
        feishu_target_chat_id="oc_test",
        feishu_approvers={"ou_approver": "Primary on-call"},
        feishu_web_base_url="https://alerts.example.test",
    )


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://open.feishu.test/open-apis",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_send_card_caches_tenant_token_and_encodes_card_content() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        assert request.headers["Authorization"] == "Bearer tenant-token"
        body = json.loads(request.content)
        assert body["receive_id"] == "oc_test"
        assert json.loads(body["content"])["schema"] == "2.0"
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_test"}})

    redis = FakeRedis()
    http_client = transport(handler)
    client = FeishuClient(settings=worker_settings(), redis=redis, http_client=http_client)
    try:
        assert (
            await client.send_card(
                receive_id="oc_test",
                receive_id_type="chat_id",
                card={"schema": "2.0"},
            )
            == "om_test"
        )
        assert (
            await client.send_card(
                receive_id="oc_test",
                receive_id_type="chat_id",
                card={"schema": "2.0"},
            )
            == "om_test"
        )
    finally:
        await http_client.aclose()
    assert redis.set_calls == 1
    assert sum("tenant_access_token" in request.url.path for request in requests) == 1


@pytest.mark.asyncio
async def test_auth_failure_clears_token_and_refreshes_once() -> None:
    token_calls = 0
    message_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, message_calls
        if "tenant_access_token" in request.url.path:
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": f"token-{token_calls}",
                    "expire": 7200,
                },
            )
        message_calls += 1
        if message_calls == 1:
            return httpx.Response(401, json={"code": 99991663, "msg": "expired"})
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_new"}})

    redis = FakeRedis()
    http_client = transport(handler)
    client = FeishuClient(settings=worker_settings(), redis=redis, http_client=http_client)
    try:
        assert (
            await client.send_card(
                receive_id="oc_test",
                receive_id_type="chat_id",
                card={"schema": "2.0"},
            )
            == "om_new"
        )
    finally:
        await http_client.aclose()
    assert token_calls == 2
    assert redis.delete_calls == 1


@pytest.mark.asyncio
async def test_patch_expired_message_requests_replacement_without_leaking_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "tenant_access_token" in request.url.path:
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7200},
            )
        return httpx.Response(
            400,
            json={"code": 230011, "msg": "deleted card with sensitive supplier detail"},
        )

    http_client = transport(handler)
    client = FeishuClient(settings=worker_settings(), redis=FakeRedis(), http_client=http_client)
    try:
        with pytest.raises(FeishuApiError) as captured:
            await client.update_card(message_id="om_old", card={"schema": "2.0"})
    finally:
        await http_client.aclose()
    assert captured.value.replace_message is True
    assert "sensitive supplier detail" not in str(captured.value)
