import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from app.schemas.workflow import (
    DiagnosisDraftPayload,
    EvidenceItem,
    RecommendationItem,
    RootCauseItem,
)
from app.workflows.alert.state import ContextSnapshot


@dataclass(frozen=True, slots=True)
class ContextProviderResult:
    data: dict[str, JsonValue]
    source_refs: list[str]
    status: str = "succeeded"


class ContextProvider(Protocol):
    name: str

    async def collect(self, alert: Mapping[str, JsonValue]) -> ContextProviderResult: ...


class DiagnosticModel(Protocol):
    async def diagnose(
        self,
        *,
        alert: Mapping[str, JsonValue],
        classification: Mapping[str, JsonValue],
        contexts: Mapping[str, ContextSnapshot],
        feedback: str | None,
    ) -> object: ...

    async def recommend(self, diagnosis: DiagnosisDraftPayload) -> object: ...


class MockMetricsProvider:
    name = "metrics"

    async def collect(self, alert: Mapping[str, JsonValue]) -> ContextProviderResult:
        await asyncio.sleep(0)
        payload = alert.get("payload")
        payload_values = payload if isinstance(payload, Mapping) else {}
        return ContextProviderResult(
            data={
                "cpu_percent": payload_values.get("value", 92.5),
                "threshold": payload_values.get("threshold", 80),
                "window_minutes": 5,
            },
            source_refs=["prometheus://cpu/order-service/5m"],
        )


class MockLogsProvider:
    name = "logs"

    async def collect(self, alert: Mapping[str, JsonValue]) -> ContextProviderResult:
        await asyncio.sleep(0)
        service = str(alert.get("service", "unknown-service"))
        return ContextProviderResult(
            data={
                "service": service,
                "error_count": 18,
                "sample": "database query exceeded two seconds",
            },
            source_refs=[f"loki://{service}?window=5m"],
        )


class MockCmdbProvider:
    name = "cmdb"

    async def collect(self, alert: Mapping[str, JsonValue]) -> ContextProviderResult:
        await asyncio.sleep(0)
        service = str(alert.get("service", "unknown-service"))
        return ContextProviderResult(
            data={
                "service": service,
                "owner": "commerce-platform",
                "tier": "critical",
                "recent_change": "release-2026.07.19.1",
            },
            source_refs=[f"cmdb://services/{service}"],
        )


class MockKnowledgeProvider:
    name = "knowledge"

    async def collect(self, alert: Mapping[str, JsonValue]) -> ContextProviderResult:
        await asyncio.sleep(0)
        return ContextProviderResult(
            data={
                "document": "CPU saturation troubleshooting playbook",
                "chapter": "Database bottlenecks",
                "matched_steps": ["inspect slow queries", "compare deployment timeline"],
            },
            source_refs=["kb://runbooks/cpu-saturation#database"],
        )


def default_context_providers() -> tuple[ContextProvider, ...]:
    return (
        MockMetricsProvider(),
        MockLogsProvider(),
        MockCmdbProvider(),
        MockKnowledgeProvider(),
    )


class MockDiagnosticModel:
    """Deterministic offline adapter used until a real LLM is configured."""

    async def diagnose(
        self,
        *,
        alert: Mapping[str, JsonValue],
        classification: Mapping[str, JsonValue],
        contexts: Mapping[str, ContextSnapshot],
        feedback: str | None,
    ) -> object:
        del alert, classification
        evidence: list[EvidenceItem] = []
        evidence_type = {
            "metrics": "metric",
            "logs": "log",
            "cmdb": "cmdb",
            "knowledge": "knowledge",
        }
        for provider_name, snapshot in sorted(contexts.items()):
            evidence.append(
                EvidenceItem(
                    id=f"{provider_name}-evidence",
                    type=evidence_type.get(provider_name, "knowledge"),
                    source=provider_name,
                    title=f"{provider_name.title()} context",
                    content=json.dumps(snapshot.data, ensure_ascii=False, sort_keys=True),
                    reference=snapshot.source_refs[0] if snapshot.source_refs else None,
                    score=0.9 if snapshot.status == "succeeded" else 0.7,
                )
            )
        if not evidence:
            raise ValueError("diagnosis requires at least one successful context provider")

        evidence_refs = [item.id for item in evidence]
        summary = "CPU saturation correlates with slow database activity after a recent release."
        if feedback:
            summary = f"Reanalysis considered operator feedback: {feedback}"
        return DiagnosisDraftPayload(
            summary=summary,
            root_causes=[
                RootCauseItem(
                    title="Database workload regression",
                    explanation=(
                        "Metrics, logs, topology and the runbook consistently point to a "
                        "database-bound request path."
                    ),
                    confidence=0.91,
                    evidence_refs=evidence_refs,
                )
            ],
            evidence=evidence,
            confidence=0.91,
            model_name="mock-diagnostic-model",
            prompt_version="diagnosis-v1",
        ).model_dump(mode="json")

    async def recommend(self, diagnosis: DiagnosisDraftPayload) -> object:
        del diagnosis
        return [
            RecommendationItem(
                title="Inspect the query plan",
                rationale="Confirm the database bottleneck before changing production.",
                risk="low",
                actions=[
                    "Run EXPLAIN ANALYZE in a read-only session",
                    "Compare the query plan with the previous release",
                ],
            ).model_dump(mode="json")
        ]
