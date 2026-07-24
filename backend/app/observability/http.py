import logging
from time import monotonic
from typing import Any
from uuid import uuid4

from starlette.routing import compile_path

from app.observability.logging import bind_log_context, normalize_context
from app.observability.metrics import observe_http_request

logger = logging.getLogger(__name__)


class _RouteTemplateResolver:
    def __init__(self) -> None:
        self._route_patterns: tuple[tuple[str, Any], ...] | None = None

    def resolve(self, scope: dict[str, Any]) -> str:
        if self._route_patterns is None:
            try:
                application = scope["app"]
                paths = application.openapi().get("paths", {})
                self._route_patterns = tuple(
                    (str(path), compile_path(str(path))[0]) for path in paths
                )
            except Exception:  # noqa: BLE001 - telemetry must not affect requests
                self._route_patterns = ()
        request_path = str(scope.get("path", ""))
        for route_path, pattern in self._route_patterns:
            if pattern.match(request_path):
                return route_path
        return "unmatched"


class RequestObservabilityMiddleware:
    """Create a trusted request ID and emit one bounded structured access log."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._routes = _RouteTemplateResolver()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        client_request_id = self._header(scope, b"x-client-request-id")
        correlation = normalize_context(
            {"request_id": request_id, "client_request_id": client_request_id}
        )
        started = monotonic()
        status_code = 500

        async def add_request_id(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with bind_log_context(**correlation):
            error_type: str | None = None
            try:
                await self.app(scope, receive, add_request_id)
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                if scope.get("path") != "/metrics":
                    route = self._routes.resolve(scope)
                    path_params = scope.get("path_params") or {}
                    extra: dict[str, object] = {
                        "method": str(scope.get("method", "UNKNOWN")),
                        "route": str(route),
                        "status_code": status_code,
                        "duration_ms": round(max(0.0, monotonic() - started) * 1000, 3),
                        "error_type": error_type,
                    }
                    alert_id = normalize_context({"alert_id": path_params.get("alert_id")})
                    extra.update(alert_id)
                    logger.log(
                        logging.ERROR if status_code >= 500 else logging.INFO,
                        "http.request.completed",
                        extra=extra,
                    )

    @staticmethod
    def _header(scope: dict[str, Any], name: bytes) -> str | None:
        for key, value in scope.get("headers", []):
            if key.lower() == name:
                return value.decode("latin-1")
        return None


class PrometheusMetricsMiddleware:
    """Record bounded HTTP labels without changing application behavior."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._routes = _RouteTemplateResolver()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        started = monotonic()
        status_code = 500
        route_path = self._routes.resolve(scope)

        async def capture_status(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            observe_http_request(
                method=str(scope.get("method", "UNKNOWN")),
                route=route_path,
                status_code=status_code,
                duration_seconds=max(0.0, monotonic() - started),
            )
