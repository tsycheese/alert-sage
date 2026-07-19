from collections.abc import Sequence
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.workflows.alert.adapters import (
    ContextProvider,
    DiagnosticModel,
    MockDiagnosticModel,
    default_context_providers,
)
from app.workflows.alert.nodes import AlertWorkflowNodes
from app.workflows.alert.state import AlertGraphState


def build_alert_graph(
    *,
    checkpointer: Any,
    context_providers: Sequence[ContextProvider] | None = None,
    diagnostic_model: DiagnosticModel | None = None,
    tool_timeout_seconds: float = 1.0,
    tool_max_attempts: int = 2,
) -> Any:
    nodes = AlertWorkflowNodes(
        context_providers=context_providers or default_context_providers(),
        diagnostic_model=diagnostic_model or MockDiagnosticModel(),
        tool_timeout_seconds=tool_timeout_seconds,
        tool_max_attempts=tool_max_attempts,
    )
    builder = StateGraph(AlertGraphState)
    builder.add_node("parse_alert", nodes.parse_alert)
    builder.add_node("classify_alert", nodes.classify_alert)
    builder.add_node("collect_context", nodes.collect_context)
    builder.add_node("diagnose", nodes.diagnose)
    builder.add_node("recommend", nodes.recommend)
    builder.add_node("human_review", nodes.human_review)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "parse_alert")
    builder.add_edge("parse_alert", "classify_alert")
    builder.add_edge("classify_alert", "collect_context")
    builder.add_edge("collect_context", "diagnose")
    builder.add_edge("diagnose", "recommend")
    builder.add_edge("recommend", "human_review")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer, name="alert-diagnosis-v1")
