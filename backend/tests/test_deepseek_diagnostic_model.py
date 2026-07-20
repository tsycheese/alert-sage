import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.llm.factory import create_diagnostic_model
from app.integrations.llm.openai_compatible import (
    DiagnosticModelAuthenticationError,
    DiagnosticModelResponseError,
    OpenAICompatibleDiagnosticModel,
    OpenAICompatibleModelConfig,
)
from app.schemas.workflow import DiagnosisDraftPayload
from app.workflows.alert.adapters import MockDiagnosticModel
from app.workflows.alert.state import ContextSnapshot


def model_adapter(handler: httpx.MockTransport) -> OpenAICompatibleDiagnosticModel:
    return OpenAICompatibleDiagnosticModel(
        OpenAICompatibleModelConfig(
            base_url="https://api.deepseek.test",
            api_key="deepseek-test-secret",
            model="deepseek-v4-flash",
            timeout_seconds=1,
            max_retries=1,
            max_tokens=3000,
            temperature=0.1,
            thinking_enabled=False,
        ),
        transport=handler,
    )


def contexts() -> dict[str, ContextSnapshot]:
    collected_at = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)
    return {
        "metrics": ContextSnapshot(
            provider="metrics",
            status="succeeded",
            data={"cpu_percent": 95.2, "threshold": 80},
            source_refs=["prometheus://cpu/checkout/5m"],
            collected_at=collected_at,
        ),
        "knowledge": ContextSnapshot(
            provider="knowledge",
            status="succeeded",
            data={
                "matches": [
                    {
                        "document_id": "document-1",
                        "content": "A previous incident was caused by a slow query.",
                        "score": 0.82,
                        "source": "dify://case/segment-1",
                    }
                ]
            },
            source_refs=["dify://case/segment-1"],
            collected_at=collected_at,
        ),
    }


def diagnosis_content() -> str:
    return json.dumps(
        {
            "summary": "CPU saturation is correlated with a database regression.",
            "root_causes": [
                {
                    "title": "Slow database query",
                    "explanation": "Metrics and the historical case support this cause.",
                    "confidence": 0.88,
                    "evidence_refs": ["metrics-evidence", "knowledge-evidence"],
                }
            ],
            "confidence": 0.88,
        }
    )


def completion(content: str, *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "completion-1",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_deepseek_diagnosis_uses_json_mode_and_deterministic_evidence() -> None:
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers["authorization"] == "Bearer deepseek-test-secret"
        request_bodies.append(json.loads(request.content))
        return completion(diagnosis_content())

    adapter = model_adapter(httpx.MockTransport(handler))
    raw_result = await adapter.diagnose(
        alert={
            "alert_name": "HighCPUUsage",
            "service": "checkout-service",
            "instance": "checkout-01",
            "severity": "critical",
            "payload": {"summary": "CPU exceeded the threshold"},
        },
        classification={"category": "resource_saturation"},
        contexts=contexts(),
        feedback=None,
    )
    result = DiagnosisDraftPayload.model_validate(raw_result)

    assert request_bodies[0]["model"] == "deepseek-v4-flash"
    assert request_bodies[0]["response_format"] == {"type": "json_object"}
    assert request_bodies[0]["thinking"] == {"type": "disabled"}
    assert request_bodies[0]["stream"] is False
    assert "Return one JSON object" in request_bodies[0]["messages"][0]["content"]
    assert result.model_name == "deepseek-v4-flash"
    assert result.prompt_version == "diagnosis-deepseek-v1"
    assert [item.id for item in result.evidence] == [
        "knowledge-evidence",
        "metrics-evidence",
    ]
    assert result.evidence[0].reference == "dify://case/segment-1"
    assert result.evidence[0].score == 0.82


@pytest.mark.asyncio
async def test_deepseek_recommendations_are_structured_and_require_human_approval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system_prompt = body["messages"][0]["content"]
        assert "requiring human approval" in system_prompt
        return completion(
            json.dumps(
                {
                    "recommendations": [
                        {
                            "title": "Inspect the slow query",
                            "rationale": "Validate the suspected cause before remediation.",
                            "risk": "low",
                            "actions": ["Run EXPLAIN in a read-only session"],
                        }
                    ]
                }
            )
        )

    adapter = model_adapter(httpx.MockTransport(handler))
    diagnosis = DiagnosisDraftPayload.model_validate(
        {
            "summary": "Database regression",
            "root_causes": [
                {
                    "title": "Slow query",
                    "explanation": "The metrics support it.",
                    "confidence": 0.8,
                    "evidence_refs": ["metrics-evidence"],
                }
            ],
            "evidence": [
                {
                    "id": "metrics-evidence",
                    "type": "metric",
                    "source": "metrics",
                    "title": "Metrics context",
                    "content": "CPU 95%",
                    "reference": "prometheus://cpu/checkout/5m",
                    "score": 0.9,
                }
            ],
            "confidence": 0.8,
            "model_name": "deepseek-v4-flash",
            "prompt_version": "diagnosis-deepseek-v1",
        }
    )

    recommendations = await adapter.recommend(diagnosis)

    assert recommendations == [
        {
            "title": "Inspect the slow query",
            "rationale": "Validate the suspected cause before remediation.",
            "risk": "low",
            "actions": ["Run EXPLAIN in a read-only session"],
        }
    ]


@pytest.mark.asyncio
async def test_deepseek_repairs_invalid_schema_once() -> None:
    attempts = 0
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        request_bodies.append(json.loads(request.content))
        if attempts == 1:
            return completion('{"summary":"missing required fields"}')
        return completion(diagnosis_content())

    adapter = model_adapter(httpx.MockTransport(handler))
    result = await adapter.diagnose(
        alert={"alert_name": "HighCPUUsage", "service": "checkout", "severity": "critical"},
        classification={"category": "resource_saturation"},
        contexts=contexts(),
        feedback="Check the latest release",
    )

    assert DiagnosisDraftPayload.model_validate(result).confidence == 0.88
    assert attempts == 2
    assert len(request_bodies[1]["messages"]) == 4
    assert "schema validation failed" in request_bodies[1]["messages"][3]["content"]


@pytest.mark.asyncio
async def test_deepseek_repairs_unknown_evidence_reference() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            invalid = json.loads(diagnosis_content())
            invalid["root_causes"][0]["evidence_refs"] = ["invented-evidence"]
            return completion(json.dumps(invalid))
        return completion(diagnosis_content())

    adapter = model_adapter(httpx.MockTransport(handler))
    result = await adapter.diagnose(
        alert={"alert_name": "HighCPUUsage", "service": "checkout", "severity": "critical"},
        classification={},
        contexts=contexts(),
        feedback=None,
    )

    assert DiagnosisDraftPayload.model_validate(result).confidence == 0.88
    assert attempts == 2


@pytest.mark.asyncio
async def test_deepseek_bounds_untrusted_input_and_marks_truncation() -> None:
    request_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        return completion(diagnosis_content())

    adapter = model_adapter(httpx.MockTransport(handler))
    oversized_contexts = contexts()
    oversized_contexts["knowledge"].data["matches"][0]["content"] = "x" * 10_000
    raw_result = await adapter.diagnose(
        alert={
            "alert_name": "HighCPUUsage",
            "service": "checkout",
            "severity": "critical",
            "payload": {"raw": "y" * 10_000},
        },
        classification={},
        contexts=oversized_contexts,
        feedback="z" * 10_000,
    )
    result = DiagnosisDraftPayload.model_validate(raw_result)
    user_payload = json.loads(request_body["messages"][1]["content"])

    assert len(user_payload["alert"]["payload"]) == 4000
    assert len(user_payload["operator_feedback"]) == 2000
    assert len(result.evidence[0].content) == 6000
    assert result.evidence[0].content.endswith("[truncated by Alert Sage]")


@pytest.mark.asyncio
async def test_deepseek_invalid_output_after_repair_is_sanitized() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return completion('{"content":"deepseek-test-secret"}')

    adapter = model_adapter(httpx.MockTransport(handler))
    with pytest.raises(DiagnosticModelResponseError) as captured:
        await adapter.diagnose(
            alert={"alert_name": "HighCPUUsage", "service": "checkout", "severity": "critical"},
            classification={},
            contexts=contexts(),
            feedback=None,
        )

    assert "deepseek-test-secret" not in str(captured.value)
    assert "after repair" in str(captured.value)


@pytest.mark.asyncio
async def test_deepseek_retries_rate_limit_and_hides_authentication_body() -> None:
    attempts = 0

    def rate_limit(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"message": "slow down"})
        return completion(diagnosis_content())

    adapter = model_adapter(httpx.MockTransport(rate_limit))
    result = await adapter.diagnose(
        alert={"alert_name": "HighCPUUsage", "service": "checkout", "severity": "critical"},
        classification={},
        contexts=contexts(),
        feedback=None,
    )
    assert DiagnosisDraftPayload.model_validate(result).confidence == 0.88
    assert attempts == 2

    def auth_failure(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "deepseek-test-secret is invalid"})

    auth_adapter = model_adapter(httpx.MockTransport(auth_failure))
    with pytest.raises(DiagnosticModelAuthenticationError) as captured:
        await auth_adapter.diagnose(
            alert={"alert_name": "HighCPUUsage", "service": "checkout", "severity": "critical"},
            classification={},
            contexts=contexts(),
            feedback=None,
        )
    assert "deepseek-test-secret" not in str(captured.value)


def test_deepseek_settings_and_factory_keep_secrets_out_of_repr() -> None:
    with pytest.raises(ValidationError, match="DIAGNOSTIC_MODEL_API_KEY"):
        Settings(_env_file=None, diagnostic_model_provider="deepseek")

    settings = Settings(
        _env_file=None,
        diagnostic_model_provider="deepseek",
        diagnostic_model_api_key="deepseek-live-secret",
        diagnostic_model_name="deepseek-v4-flash",
    )
    model = create_diagnostic_model(settings)

    assert isinstance(model, OpenAICompatibleDiagnosticModel)
    assert "deepseek-live-secret" not in repr(settings)
    assert isinstance(
        create_diagnostic_model(Settings(_env_file=None)),
        MockDiagnosticModel,
    )
