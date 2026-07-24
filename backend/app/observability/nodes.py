import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from time import monotonic
from typing import Any

from langgraph.errors import GraphInterrupt

from app.observability.metrics import observe_workflow_node

WorkflowNode = Callable[[Any], Awaitable[Any]]
logger = logging.getLogger(__name__)


def observed_node(name: str, node: WorkflowNode) -> WorkflowNode:
    @wraps(node)
    async def invoke(graph_state: Any) -> Any:
        started = monotonic()
        status = "error"
        error_type: str | None = None
        logger.info("workflow.node.started", extra={"node": name})
        try:
            result = await node(graph_state)
            status = "success"
            return result
        except GraphInterrupt:
            status = "interrupted"
            raise
        except asyncio.CancelledError as exc:
            status = "cancelled"
            error_type = type(exc).__name__
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            duration_seconds = max(0.0, monotonic() - started)
            observe_workflow_node(
                node=name,
                duration_seconds=duration_seconds,
            )
            logger.log(
                logging.ERROR if error_type and status != "cancelled" else logging.INFO,
                "workflow.node.completed",
                extra={
                    "node": name,
                    "status": status,
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "error_type": error_type,
                },
            )

    return invoke
