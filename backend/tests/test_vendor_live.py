import os
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.integrations.knowledge.factory import create_knowledge_retriever
from app.integrations.llm.factory import create_diagnostic_model
from app.schemas.workflow import DiagnosisDraftPayload
from app.workflows.alert.state import ContextSnapshot

LIVE_ENABLED = os.getenv("ALERT_SAGE_RUN_VENDOR_LIVE_TESTS") == "1"


@pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="set ALERT_SAGE_RUN_VENDOR_LIVE_TESTS=1 with real-profile credentials",
)
@pytest.mark.asyncio
async def test_real_deepseek_and_dify_with_synthetic_alert() -> None:
    settings = Settings()
    assert settings.runtime_profile == "real"
    assert settings.component_role == "test"
    assert settings.knowledge_provider == "dify"
    assert settings.diagnostic_model_provider == "deepseek"

    retriever = create_knowledge_retriever(settings)
    chunks = await retriever.retrieve(
        "Synthetic Alert Sage acceptance: CPU saturation diagnostic procedure",
        top_k=3,
    )
    knowledge = ContextSnapshot(
        provider="knowledge",
        status="succeeded",
        data={
            "matches": [
                {
                    "document_id": chunk.document_id,
                    "content": chunk.content,
                    "score": chunk.score,
                    "source": chunk.source,
                }
                for chunk in chunks
            ]
        },
        source_refs=[chunk.source for chunk in chunks],
        collected_at=datetime.now(UTC),
    )
    metrics = ContextSnapshot(
        provider="metrics",
        status="succeeded",
        data={"cpu_percent": 92.5, "threshold": 80, "synthetic": True},
        source_refs=["synthetic://metrics/cpu/5m"],
        collected_at=datetime.now(UTC),
    )
    draft = await create_diagnostic_model(settings).diagnose(
        alert={
            "alert_name": "SyntheticHighCPU",
            "service": "synthetic-service",
            "instance": "synthetic-01",
            "severity": "critical",
            "payload": {"summary": "Synthetic, non-sensitive acceptance alert"},
        },
        classification={"category": "resource_saturation"},
        contexts={"metrics": metrics, "knowledge": knowledge},
        feedback=None,
    )
    validated = DiagnosisDraftPayload.model_validate(draft)
    assert validated.summary
    assert validated.evidence
