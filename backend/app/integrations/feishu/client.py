import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.observability.metrics import observe_feishu_rate_limit, observe_feishu_token_refresh


class FeishuApiError(RuntimeError):
    def __init__(self, code: str, *, replace_message: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.replace_message = replace_message


@dataclass(frozen=True, slots=True)
class FeishuMessageResult:
    message_id: str


class FeishuClient:
    def __init__(
        self,
        *,
        settings: Settings,
        redis: Redis,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self._client = http_client or httpx.AsyncClient(
            base_url=settings.feishu_api_base_url,
            timeout=httpx.Timeout(settings.feishu_http_timeout_seconds),
        )
        self._owns_client = http_client is None
        self._token_key = f"alert-sage:feishu:tenant-token:{settings.feishu_app_id}"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_card(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        card: dict[str, Any],
    ) -> str:
        response = await self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json_body={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False, separators=(",", ":")),
            },
        )
        message_id = str((response.get("data") or {}).get("message_id") or "").strip()
        if not message_id:
            raise FeishuApiError("invalid_message_response")
        return message_id

    async def update_card(self, *, message_id: str, card: dict[str, Any]) -> None:
        await self._request(
            "PATCH",
            f"/im/v1/messages/{message_id}",
            json_body={"content": json.dumps(card, ensure_ascii=False, separators=(",", ":"))},
            replacement_codes={230001, 230002, 230011, 230020},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any],
        replacement_codes: set[int] | None = None,
    ) -> dict[str, Any]:
        refreshed = False
        for attempt in range(self.settings.feishu_max_retries + 1):
            token = await self._tenant_token(force_refresh=refreshed)
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.settings.feishu_max_retries:
                    raise FeishuApiError("feishu_transport_error") from exc
                await asyncio.sleep(min(2**attempt, 5))
                continue
            payload = self._json_payload(response)
            code = self._provider_code(payload)
            if response.status_code in {401, 403} or code in {99991663, 99991664, 99991668}:
                if refreshed:
                    raise FeishuApiError("feishu_auth_error")
                await self._delete_cached_token()
                refreshed = True
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if response.status_code == 429:
                    observe_feishu_rate_limit(operation=path.rsplit("/", 1)[-1])
                if attempt >= self.settings.feishu_max_retries:
                    error = (
                        "feishu_rate_limited"
                        if response.status_code == 429
                        else "feishu_unavailable"
                    )
                    raise FeishuApiError(error)
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    delay = float(2**attempt)
                await asyncio.sleep(min(max(delay, 0), 10))
                continue
            if response.is_error or code != 0:
                raise FeishuApiError(
                    f"feishu_api_{code or response.status_code}",
                    replace_message=bool(replacement_codes and code in replacement_codes),
                )
            return payload
        raise FeishuApiError("feishu_retry_exhausted")

    async def _tenant_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            try:
                cached = await self.redis.get(self._token_key)
            except RedisError:
                cached = None
            if cached:
                return str(cached)
        secret = self.settings.feishu_app_secret
        if secret is None:
            raise FeishuApiError("feishu_credentials_missing")
        response: httpx.Response | None = None
        for attempt in range(self.settings.feishu_max_retries + 1):
            try:
                response = await self._client.post(
                    "/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": self.settings.feishu_app_id,
                        "app_secret": secret.get_secret_value(),
                    },
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.settings.feishu_max_retries:
                    observe_feishu_token_refresh(result="transport_error")
                    raise FeishuApiError("feishu_token_transport_error") from exc
                await asyncio.sleep(min(2**attempt, 5))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if response.status_code == 429:
                    observe_feishu_rate_limit(operation="tenant_token")
                if attempt >= self.settings.feishu_max_retries:
                    observe_feishu_token_refresh(result="failed")
                    raise FeishuApiError("feishu_token_unavailable")
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    delay = float(2**attempt)
                await asyncio.sleep(min(max(delay, 0), 10))
                continue
            break
        if response is None:
            raise FeishuApiError("feishu_token_unavailable")
        payload = self._json_payload(response)
        if response.is_error or self._provider_code(payload) != 0:
            observe_feishu_token_refresh(result="failed")
            raise FeishuApiError("feishu_token_error")
        token = str(payload.get("tenant_access_token") or "").strip()
        try:
            expires = int(payload.get("expire") or 0)
        except (TypeError, ValueError) as exc:
            raise FeishuApiError("invalid_token_response") from exc
        if not token or expires <= 0:
            observe_feishu_token_refresh(result="invalid")
            raise FeishuApiError("invalid_token_response")
        ttl = max(1, expires - self.settings.feishu_token_refresh_margin_seconds)
        try:
            await self.redis.set(self._token_key, token, ex=ttl)
        except RedisError:
            pass
        observe_feishu_token_refresh(result="succeeded")
        return token

    async def _delete_cached_token(self) -> None:
        try:
            await self.redis.delete(self._token_key)
        except RedisError:
            pass

    @staticmethod
    def _json_payload(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise FeishuApiError("feishu_non_json_response") from exc
        if not isinstance(payload, dict):
            raise FeishuApiError("feishu_non_object_response")
        return payload

    @staticmethod
    def _provider_code(payload: dict[str, Any]) -> int:
        try:
            return int(payload.get("code", 0))
        except (TypeError, ValueError):
            return -1
