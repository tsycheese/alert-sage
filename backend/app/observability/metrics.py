from collections.abc import Callable
from enum import Enum

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "alert_sage_http_requests_total",
    "HTTP requests handled by the Alert Sage API.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION = Histogram(
    "alert_sage_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
WORKFLOW_RUNS = Counter(
    "alert_sage_workflow_runs_total",
    "Workflow task executions by operation and result status.",
    ("operation", "status"),
)
WORKFLOW_DURATION = Histogram(
    "alert_sage_workflow_duration_seconds",
    "Workflow task duration in seconds.",
    ("operation", "status"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
WORKFLOW_NODE_DURATION = Histogram(
    "alert_sage_workflow_node_duration_seconds",
    "LangGraph node invocation duration in seconds.",
    ("node",),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15, 60),
)
TOOL_CALLS = Counter(
    "alert_sage_tool_calls_total",
    "Context tool calls by tool and result status.",
    ("tool", "status"),
)
TOOL_CALL_DURATION = Histogram(
    "alert_sage_tool_call_duration_seconds",
    "Context tool call duration in seconds.",
    ("tool",),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15),
)
LLM_REQUESTS = Counter(
    "alert_sage_llm_requests_total",
    "Diagnostic model requests by provider, model, operation and status.",
    ("provider", "model", "operation", "status"),
)
LLM_REQUEST_DURATION = Histogram(
    "alert_sage_llm_request_duration_seconds",
    "Diagnostic model request duration in seconds.",
    ("provider", "model", "operation"),
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
)
LLM_RETRIES = Counter(
    "alert_sage_llm_retries_total",
    "Diagnostic model transport retries by reason.",
    ("provider", "model", "operation", "reason"),
)
LLM_OUTPUT_REPAIRS = Counter(
    "alert_sage_llm_output_repairs_total",
    "Diagnostic model structured-output repairs by outcome.",
    ("provider", "model", "operation", "outcome"),
)
KNOWLEDGE_OPERATIONS = Counter(
    "alert_sage_knowledge_operations_total",
    "Knowledge adapter operations by provider, operation and status.",
    ("provider", "operation", "status"),
)
KNOWLEDGE_OPERATION_DURATION = Histogram(
    "alert_sage_knowledge_operation_duration_seconds",
    "Knowledge adapter operation duration in seconds.",
    ("provider", "operation"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15, 30, 60, 180),
)
KNOWLEDGE_RESULTS = Histogram(
    "alert_sage_knowledge_retrieval_results",
    "Number of chunks returned by a knowledge retrieval.",
    ("provider",),
    buckets=(0, 1, 2, 3, 5, 10, 20),
)
CASE_SYNCS = Counter(
    "alert_sage_case_syncs_total",
    "Case synchronization task executions by provider and status.",
    ("provider", "status"),
)
CASE_SYNC_DURATION = Histogram(
    "alert_sage_case_sync_duration_seconds",
    "Case synchronization task duration in seconds.",
    ("provider",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 180),
)
OUTBOX_DELIVERIES = Counter(
    "alert_sage_outbox_deliveries_total",
    "Transactional outbox publish attempts by bounded topic and result status.",
    ("topic", "status"),
)
OUTBOX_DELIVERY_DURATION = Histogram(
    "alert_sage_outbox_delivery_duration_seconds",
    "Transactional outbox publish duration by bounded topic.",
    ("topic",),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15),
)
OUTBOX_RETRY_DELAY = Histogram(
    "alert_sage_outbox_retry_delay_seconds",
    "Scheduled transactional outbox retry delay by bounded topic.",
    ("topic",),
    buckets=(1, 2, 5, 10, 30, 60, 120, 300),
)
FEISHU_CALLBACKS = Counter(
    "alert_sage_feishu_callbacks_total",
    "Feishu callbacks by bounded result.",
    ("result",),
)
FEISHU_CALLBACK_DURATION = Histogram(
    "alert_sage_feishu_callback_duration_seconds",
    "Feishu callback processing duration.",
    ("result",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3),
)
FEISHU_DELIVERIES = Counter(
    "alert_sage_feishu_deliveries_total",
    "Feishu card deliveries by kind, operation and bounded result.",
    ("kind", "operation", "result"),
)
FEISHU_DELIVERY_DURATION = Histogram(
    "alert_sage_feishu_delivery_duration_seconds",
    "Feishu card delivery duration.",
    ("kind", "operation"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
FEISHU_RATE_LIMITS = Counter(
    "alert_sage_feishu_rate_limits_total",
    "Feishu API rate-limit responses by operation.",
    ("operation",),
)
FEISHU_TOKEN_REFRESHES = Counter(
    "alert_sage_feishu_token_refreshes_total",
    "Feishu tenant token refreshes by result.",
    ("result",),
)
FEISHU_PENDING_DELIVERY_SNAPSHOT = Histogram(
    "alert_sage_feishu_pending_delivery_snapshot",
    "Sampled count of Feishu delivery rows still pending or processing.",
    buckets=(0, 1, 2, 5, 10, 25, 50, 100, 250, 500),
)


def label_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _record(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:  # noqa: BLE001 - telemetry must never alter business behavior
        return


def observe_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    def record() -> None:
        HTTP_REQUESTS.labels(method=method, route=route, status_code=str(status_code)).inc()
        HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration_seconds)

    _record(record)


def observe_workflow(*, operation: str, status: object, duration_seconds: float) -> None:
    normalized_status = label_value(status)

    def record() -> None:
        WORKFLOW_RUNS.labels(operation=operation, status=normalized_status).inc()
        WORKFLOW_DURATION.labels(operation=operation, status=normalized_status).observe(
            duration_seconds
        )

    _record(record)


def observe_workflow_node(*, node: str, duration_seconds: float) -> None:
    _record(lambda: WORKFLOW_NODE_DURATION.labels(node=node).observe(duration_seconds))


def observe_tool(*, tool: str, status: str, duration_seconds: float) -> None:
    def record() -> None:
        TOOL_CALLS.labels(tool=tool, status=status).inc()
        TOOL_CALL_DURATION.labels(tool=tool).observe(duration_seconds)

    _record(record)


def observe_llm_request(
    *, provider: str, model: str, operation: str, status: str, duration_seconds: float
) -> None:
    def record() -> None:
        LLM_REQUESTS.labels(
            provider=provider,
            model=model,
            operation=operation,
            status=status,
        ).inc()
        LLM_REQUEST_DURATION.labels(
            provider=provider,
            model=model,
            operation=operation,
        ).observe(duration_seconds)

    _record(record)


def increment_llm_retry(*, provider: str, model: str, operation: str, reason: str) -> None:
    _record(
        lambda: LLM_RETRIES.labels(
            provider=provider,
            model=model,
            operation=operation,
            reason=reason,
        ).inc()
    )


def observe_llm_repair(*, provider: str, model: str, operation: str, outcome: str) -> None:
    _record(
        lambda: LLM_OUTPUT_REPAIRS.labels(
            provider=provider,
            model=model,
            operation=operation,
            outcome=outcome,
        ).inc()
    )


def observe_knowledge_operation(
    *, provider: str, operation: str, status: str, duration_seconds: float
) -> None:
    def record() -> None:
        KNOWLEDGE_OPERATIONS.labels(
            provider=provider,
            operation=operation,
            status=status,
        ).inc()
        KNOWLEDGE_OPERATION_DURATION.labels(
            provider=provider,
            operation=operation,
        ).observe(duration_seconds)

    _record(record)


def observe_knowledge_results(*, provider: str, count: int) -> None:
    _record(lambda: KNOWLEDGE_RESULTS.labels(provider=provider).observe(count))


def observe_case_sync(*, provider: str, status: str, duration_seconds: float) -> None:
    def record() -> None:
        CASE_SYNCS.labels(provider=provider, status=status).inc()
        CASE_SYNC_DURATION.labels(provider=provider).observe(duration_seconds)

    _record(record)


def observe_outbox_delivery(
    *,
    topic: str,
    status: str,
    duration_seconds: float,
    retry_delay_seconds: float | None = None,
) -> None:
    def record() -> None:
        OUTBOX_DELIVERIES.labels(topic=topic, status=status).inc()
        OUTBOX_DELIVERY_DURATION.labels(topic=topic).observe(duration_seconds)
        if retry_delay_seconds is not None:
            OUTBOX_RETRY_DELAY.labels(topic=topic).observe(retry_delay_seconds)

    _record(record)


def observe_feishu_callback(*, result: str, duration_seconds: float) -> None:
    def record() -> None:
        FEISHU_CALLBACKS.labels(result=result).inc()
        FEISHU_CALLBACK_DURATION.labels(result=result).observe(duration_seconds)

    _record(record)


def observe_feishu_delivery(
    *, kind: str, operation: str, result: str, duration_seconds: float
) -> None:
    def record() -> None:
        FEISHU_DELIVERIES.labels(kind=kind, operation=operation, result=result).inc()
        FEISHU_DELIVERY_DURATION.labels(kind=kind, operation=operation).observe(duration_seconds)

    _record(record)


def observe_feishu_rate_limit(*, operation: str) -> None:
    _record(lambda: FEISHU_RATE_LIMITS.labels(operation=operation).inc())


def observe_feishu_token_refresh(*, result: str) -> None:
    _record(lambda: FEISHU_TOKEN_REFRESHES.labels(result=result).inc())


def observe_feishu_pending_deliveries(count: int) -> None:
    _record(lambda: FEISHU_PENDING_DELIVERY_SNAPSHOT.observe(max(0, count)))
