import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from uuid import uuid5

from langgraph.types import Command, interrupt
from pydantic import TypeAdapter

from app.models.enums import HumanDecisionAction
from app.observability.metrics import observe_tool
from app.schemas.workflow import (
    DiagnosisDraftPayload,
    DiagnosisReportPayload,
    RecommendationItem,
    WorkflowResumePayload,
)
from app.workflows.alert.adapters import ContextProvider, DiagnosticModel
from app.workflows.alert.state import (
    AlertGraphState,
    ContextSnapshot,
    WorkflowHumanDecision,
    WorkflowToolError,
    validate_graph_state,
)

RecommendationListAdapter = TypeAdapter(list[RecommendationItem])
logger = logging.getLogger(__name__)


class AlertWorkflowNodes:
    def __init__(
        self,
        *,
        context_providers: Sequence[ContextProvider],
        diagnostic_model: DiagnosticModel,
        tool_timeout_seconds: float = 1.0,
        tool_max_attempts: int = 2,
    ) -> None:
        if not context_providers:
            raise ValueError("at least one context provider is required")
        provider_names = [provider.name for provider in context_providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("context provider names must be unique")
        self.context_providers = tuple(context_providers)
        self.diagnostic_model = diagnostic_model
        self.tool_timeout_seconds = tool_timeout_seconds
        self.tool_max_attempts = tool_max_attempts

    async def parse_alert(self, graph_state: AlertGraphState) -> AlertGraphState:
        state = validate_graph_state(graph_state)
        alert = dict(state.alert)
        for field in ("alert_name", "service", "severity"):
            value = alert.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"alert.{field} must be a non-empty string")
            alert[field] = value.strip()
        return {"alert": alert}

    async def classify_alert(self, graph_state: AlertGraphState) -> AlertGraphState:
        state = validate_graph_state(graph_state)
        alert_name = str(state.alert["alert_name"]).casefold()
        category = "resource_saturation" if "cpu" in alert_name else "application_failure"
        return {
            "classification": {
                "category": category,
                "urgency": state.alert["severity"],
                "requires_human_review": True,
            }
        }

    async def collect_context(self, graph_state: AlertGraphState) -> AlertGraphState:
        state = validate_graph_state(graph_state)
        results = await asyncio.gather(
            *(self._collect_one(provider, state.alert) for provider in self.context_providers)
        )
        contexts: dict[str, dict[str, object]] = {}
        errors: list[dict[str, object]] = []
        for provider_name, snapshot, error in results:
            if snapshot is not None:
                contexts[provider_name] = snapshot.model_dump(mode="json")
            if error is not None:
                errors.append(error.model_dump(mode="json"))
        warnings = list(state.warnings)
        if errors:
            warnings.append(
                "Some context providers failed; diagnosis uses the available evidence only."
            )
        return {
            "contexts": contexts,
            "tool_errors": errors,
            "warnings": warnings,
        }

    async def diagnose(self, graph_state: AlertGraphState) -> AlertGraphState:
        state = validate_graph_state(graph_state)
        feedback = None
        if (
            state.human_decision is not None
            and state.human_decision.action is HumanDecisionAction.REANALYZE
        ):
            feedback = state.human_decision.comment
        raw_diagnosis = await self.diagnostic_model.diagnose(
            alert=state.alert,
            classification=state.classification or {},
            contexts=state.contexts,
            feedback=feedback,
        )
        diagnosis = DiagnosisDraftPayload.model_validate(raw_diagnosis)
        return {"diagnosis": diagnosis.model_dump(mode="json")}

    async def recommend(self, graph_state: AlertGraphState) -> AlertGraphState:
        state = validate_graph_state(graph_state)
        diagnosis = DiagnosisDraftPayload.model_validate(state.diagnosis)
        raw_recommendations = await self.diagnostic_model.recommend(diagnosis)
        recommendations = RecommendationListAdapter.validate_python(raw_recommendations)
        report = DiagnosisReportPayload(
            summary=diagnosis.summary,
            root_causes=diagnosis.root_causes,
            evidence=diagnosis.evidence,
            recommendations=recommendations,
            confidence=diagnosis.confidence,
            model_name=diagnosis.model_name,
            prompt_version=diagnosis.prompt_version,
        )
        report_version = state.report_version + 1
        report_id = uuid5(state.workflow_run_id, f"diagnosis-report:{report_version}")
        return {
            "diagnosis": report.model_dump(mode="json", exclude={"recommendations"}),
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
            "report_id": str(report_id),
            "report_version": report_version,
            "human_decision": None,
            "decision_idempotency_key": None,
            "final_status": None,
        }

    async def human_review(
        self, graph_state: AlertGraphState
    ) -> Command[Literal["diagnose", "finalize"]]:
        state = validate_graph_state(graph_state)
        resume_value = interrupt(
            {
                "kind": "human_review",
                "workflow_run_id": str(state.workflow_run_id),
                "report_id": str(state.report_id),
                "report_version": state.report_version,
                "allowed_actions": [action.value for action in HumanDecisionAction],
            }
        )
        resume = WorkflowResumePayload.model_validate(resume_value)
        decision = WorkflowHumanDecision(
            action=resume.action,
            comment=resume.comment,
            actor=resume.actor,
        )
        update: AlertGraphState = {
            "human_decision": decision.model_dump(mode="json"),
            "decision_idempotency_key": resume.idempotency_key,
        }
        if resume.action is HumanDecisionAction.REANALYZE:
            update["reanalysis_count"] = state.reanalysis_count + 1
            return Command(update=update, goto="diagnose")
        return Command(update=update, goto="finalize")

    async def finalize(self, graph_state: AlertGraphState) -> AlertGraphState:
        state = validate_graph_state(graph_state)
        if state.human_decision is None:
            raise ValueError("finalize requires a human decision")
        if state.human_decision.action is HumanDecisionAction.APPROVE:
            return {"final_status": "completed"}
        if state.human_decision.action is HumanDecisionAction.REJECT:
            return {"final_status": "rejected"}
        raise ValueError("reanalyze decisions must return to diagnose")

    async def _collect_one(
        self,
        provider: ContextProvider,
        alert: dict[str, object],
    ) -> tuple[str, ContextSnapshot | None, WorkflowToolError | None]:
        started = monotonic()
        last_error: Exception | None = None
        for attempt in range(1, self.tool_max_attempts + 1):
            try:
                result = await asyncio.wait_for(
                    provider.collect(alert),
                    timeout=self.tool_timeout_seconds,
                )
                duration_ms = max(0, int((monotonic() - started) * 1000))
                snapshot = ContextSnapshot(
                    provider=provider.name,
                    status=result.status,
                    data=result.data,
                    source_refs=result.source_refs,
                    collected_at=datetime.now(UTC),
                    attempts=attempt,
                    duration_ms=duration_ms,
                )
                observe_tool(
                    tool=provider.name,
                    status=result.status,
                    duration_seconds=duration_ms / 1000,
                )
                logger.info(
                    "workflow.tool.completed",
                    extra={
                        "node": "collect_context",
                        "tool": provider.name,
                        "status": result.status,
                        "attempt": attempt,
                        "duration_ms": duration_ms,
                    },
                )
                return provider.name, snapshot, None
            except TimeoutError as exc:
                last_error = exc
                error_code = "tool_timeout"
            except Exception as exc:  # noqa: BLE001 - adapter failures are isolated by design
                last_error = exc
                error_code = "tool_failure"

        duration_ms = max(0, int((monotonic() - started) * 1000))
        error = WorkflowToolError(
            node_name="collect_context",
            tool_name=provider.name,
            code=error_code,
            message=str(last_error) or error_code,
            retryable=True,
            attempts=self.tool_max_attempts,
            duration_ms=duration_ms,
        )
        observe_tool(
            tool=provider.name,
            status=error_code,
            duration_seconds=duration_ms / 1000,
        )
        logger.warning(
            "workflow.tool.completed",
            extra={
                "node": "collect_context",
                "tool": provider.name,
                "status": error_code,
                "attempt": self.tool_max_attempts,
                "duration_ms": duration_ms,
                "error_type": type(last_error).__name__ if last_error is not None else None,
                "error_code": error_code,
            },
        )
        return provider.name, None, error
