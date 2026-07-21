from time import monotonic
from typing import Any

from starlette.routing import compile_path

from app.observability.metrics import observe_http_request


class PrometheusMetricsMiddleware:
    """Record bounded HTTP labels without changing application behavior."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._route_patterns: tuple[tuple[str, Any], ...] | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        started = monotonic()
        status_code = 500
        route_path = self._resolve_route_path(scope)

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

    def _resolve_route_path(self, scope: dict[str, Any]) -> str:
        """Resolve the declared route without exposing concrete URL parameters."""
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
