from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

CORRELATION_FIELDS = (
    "request_id",
    "client_request_id",
    "alert_id",
    "workflow_run_id",
    "thread_id",
    "case_id",
    "outbox_message_id",
)
CONTEXT_FIELDS = (*CORRELATION_FIELDS, "task_id")
LOG_FIELDS = (
    *CONTEXT_FIELDS,
    "operation",
    "node",
    "tool",
    "provider",
    "model",
    "method",
    "route",
    "status_code",
    "status",
    "duration_ms",
    "attempt",
    "result_count",
    "error_type",
    "error_code",
    "topic",
)
CELERY_HEADER_PREFIX = "alert_sage_"

_context: ContextVar[dict[str, str] | None] = ContextVar("alert_sage_log_context", default=None)
_safe_identifier = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_bearer = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_credential = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|token)\b"
    r"(\s*[:=]\s*)[^\s,;]+"
)
_url_password = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<user>[^\s:/@]+):[^\s@/]+@")


def _as_uuid(value: object) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _as_identifier(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized if _safe_identifier.fullmatch(normalized) else None


def normalize_context(fields: Mapping[str, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in fields.items():
        if key not in CONTEXT_FIELDS or value is None:
            continue
        if key in {
            "request_id",
            "alert_id",
            "workflow_run_id",
            "case_id",
            "outbox_message_id",
        }:
            safe_value = _as_uuid(value)
        else:
            safe_value = _as_identifier(value)
        if safe_value is not None:
            normalized[key] = safe_value
    return normalized


def get_log_context() -> dict[str, str]:
    return dict(_context.get() or {})


@contextmanager
def bind_log_context(**fields: object) -> Iterator[dict[str, str]]:
    updated = get_log_context()
    updated.update(normalize_context(fields))
    token = _context.set(updated)
    try:
        yield updated
    finally:
        _context.reset(token)


def celery_correlation_headers() -> dict[str, str]:
    return {
        f"{CELERY_HEADER_PREFIX}{key}": value
        for key, value in get_log_context().items()
        if key in CORRELATION_FIELDS
    }


def correlation_from_celery_headers(headers: Mapping[str, object] | None) -> dict[str, str]:
    raw_headers = headers or {}
    values = {key: raw_headers.get(f"{CELERY_HEADER_PREFIX}{key}") for key in CORRELATION_FIELDS}
    normalized = normalize_context(values)
    normalized.setdefault("request_id", str(uuid4()))
    return normalized


def event_correlation_payload() -> dict[str, str]:
    context = get_log_context()
    return {key: context[key] for key in ("request_id", "client_request_id") if key in context}


def redact_log_text(value: object) -> str:
    text = str(value)
    text = _bearer.sub("Bearer [REDACTED]", text)
    text = _credential.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    return _url_password.sub(
        lambda match: f"{match.group('scheme')}{match.group('user')}:[REDACTED]@",
        text,
    )


class JsonLogFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "environment": self.environment,
            "logger": record.name,
            "event": redact_log_text(record.getMessage()),
        }
        context = get_log_context()
        for field in LOG_FIELDS:
            value = getattr(record, field, context.get(field))
            if value is not None:
                payload[field] = redact_log_text(value) if isinstance(value, str) else value
        if record.exc_info and record.exc_info[0] is not None:
            payload.setdefault("error_type", record.exc_info[0].__name__)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(*, service: str, environment: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter(service=service, environment=environment))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for logger_name in ("uvicorn", "uvicorn.error"):
        framework_logger = logging.getLogger(logger_name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
