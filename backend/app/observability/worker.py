import os
from threading import Thread
from typing import Any

from celery.signals import worker_ready, worker_shutdown
from prometheus_client import REGISTRY, CollectorRegistry, multiprocess, start_http_server

from app.core.config import get_settings

_server: Any = None
_thread: Thread | None = None
_configured = False


def configure_worker_metrics() -> None:
    global _configured
    if _configured:
        return
    worker_ready.connect(_start_worker_metrics, weak=False)
    worker_shutdown.connect(_stop_worker_metrics, weak=False)
    _configured = True


def _start_worker_metrics(**_: object) -> None:
    global _server, _thread
    settings = get_settings()
    if not settings.metrics_enabled or _server is not None:
        return
    registry = REGISTRY
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    _server, _thread = start_http_server(
        settings.worker_metrics_port,
        addr="0.0.0.0",
        registry=registry,
    )


def _stop_worker_metrics(**_: object) -> None:
    global _server, _thread
    if _server is None:
        return
    _server.shutdown()
    _server.server_close()
    if _thread is not None:
        _thread.join(timeout=5)
    _server = None
    _thread = None
