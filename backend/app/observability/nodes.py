from collections.abc import Awaitable, Callable
from functools import wraps
from time import monotonic
from typing import Any

from app.observability.metrics import observe_workflow_node

WorkflowNode = Callable[[Any], Awaitable[Any]]


def observed_node(name: str, node: WorkflowNode) -> WorkflowNode:
    @wraps(node)
    async def invoke(graph_state: Any) -> Any:
        started = monotonic()
        try:
            return await node(graph_state)
        finally:
            observe_workflow_node(
                node=name,
                duration_seconds=max(0.0, monotonic() - started),
            )

    return invoke
