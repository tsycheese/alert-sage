from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from app.integrations.knowledge.retrieval import DocumentChunk
from app.main import app
from app.models.enums import KnowledgeSyncStatus
from app.observability.adapters import InstrumentedKnowledgeRetriever
from app.observability.metrics import label_value
from app.observability.nodes import observed_node
from app.services.cases import CaseSyncResult
from app.tasks import cases as case_tasks
from scripts.run_worker import prepare_multiprocess_directory


def sample_delta(name: str, labels: dict[str, str], before: float | None) -> float:
    after = REGISTRY.get_sample_value(name, labels)
    return (after or 0) - (before or 0)


@pytest.mark.asyncio
async def test_metrics_endpoint_normalizes_routes_and_excludes_itself() -> None:
    health_labels = {
        "method": "GET",
        "route": "/api/v1/health/live",
        "status_code": "200",
    }
    unmatched_labels = {"method": "GET", "route": "unmatched", "status_code": "404"}
    health_before = REGISTRY.get_sample_value("alert_sage_http_requests_total", health_labels)
    unmatched_before = REGISTRY.get_sample_value("alert_sage_http_requests_total", unmatched_labels)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/health/live")).status_code == 200
        assert (await client.get("/missing/unique-alert-id")).status_code == 404
        first_metrics = await client.get("/metrics")
        second_metrics = await client.get("/metrics")

    assert first_metrics.status_code == 200
    assert first_metrics.headers["content-type"].startswith("text/plain")
    assert sample_delta(
        "alert_sage_http_requests_total", health_labels, health_before
    ) == pytest.approx(1)
    assert sample_delta(
        "alert_sage_http_requests_total", unmatched_labels, unmatched_before
    ) == pytest.approx(1)
    assert first_metrics.text == second_metrics.text
    assert "/missing/unique-alert-id" not in second_metrics.text
    assert 'route="/metrics"' not in second_metrics.text


@pytest.mark.asyncio
async def test_observed_node_and_knowledge_adapter_record_bounded_labels() -> None:
    class Retriever:
        async def retrieve(
            self,
            query: str,
            *,
            top_k: int,
            score_threshold: float | None = None,
        ) -> list[DocumentChunk]:
            del top_k, score_threshold
            return [
                DocumentChunk(
                    id="chunk-1",
                    document_id="doc-1",
                    document_name="Runbook",
                    content=query,
                    score=0.9,
                    source="test://runbook/chunk-1",
                    metadata={},
                )
            ]

    node_labels = {"node": "test_node"}
    operation_labels = {
        "provider": "test-provider",
        "operation": "retrieve",
        "status": "success",
    }
    results_labels = {"provider": "test-provider"}
    node_before = REGISTRY.get_sample_value(
        "alert_sage_workflow_node_duration_seconds_count", node_labels
    )
    operation_before = REGISTRY.get_sample_value(
        "alert_sage_knowledge_operations_total", operation_labels
    )
    results_before = REGISTRY.get_sample_value(
        "alert_sage_knowledge_retrieval_results_count", results_labels
    )

    async def node(state: dict[str, object]) -> dict[str, object]:
        return state

    assert await observed_node("test_node", node)({"ok": True}) == {"ok": True}
    query = "unique-query-that-must-not-be-a-label"
    chunks = await InstrumentedKnowledgeRetriever(Retriever(), provider="test-provider").retrieve(
        query, top_k=3
    )

    assert chunks[0].content == query
    assert sample_delta(
        "alert_sage_workflow_node_duration_seconds_count", node_labels, node_before
    ) == pytest.approx(1)
    assert sample_delta(
        "alert_sage_knowledge_operations_total", operation_labels, operation_before
    ) == pytest.approx(1)
    assert sample_delta(
        "alert_sage_knowledge_retrieval_results_count", results_labels, results_before
    ) == pytest.approx(1)
    assert label_value("success") == "success"


def test_worker_metrics_cleanup_only_removes_prometheus_db_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    metrics_path = tmp_path / "prometheus"
    metrics_path.mkdir()
    metric_file = metrics_path / "counter_123.db"
    unrelated_file = metrics_path / "keep.txt"
    metric_file.write_text("stale", encoding="utf-8")
    unrelated_file.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(metrics_path))

    prepare_multiprocess_directory()

    assert not metric_file.exists()
    assert unrelated_file.read_text(encoding="utf-8") == "keep"


def test_worker_metrics_cleanup_rejects_filesystem_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = Path(tmp_path.anchor)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(root))

    with pytest.raises(RuntimeError, match="safe absolute directory"):
        prepare_multiprocess_directory()


def test_case_sync_task_normalizes_string_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = UUID("10000000-0000-0000-0000-000000000001")
    workflow_run_id = UUID("20000000-0000-0000-0000-000000000002")

    async def synced(_: UUID) -> CaseSyncResult:
        return CaseSyncResult(
            case_id=case_id,
            workflow_run_id=workflow_run_id,
            status=KnowledgeSyncStatus.SYNCED.value,  # type: ignore[arg-type]
            external_document_id="document-1",
        )

    labels = {"provider": "test-provider", "status": "synced"}
    before = REGISTRY.get_sample_value("alert_sage_case_syncs_total", labels)
    monkeypatch.setattr(case_tasks, "_sync_case", synced)
    monkeypatch.setattr(
        case_tasks,
        "get_settings",
        lambda: SimpleNamespace(knowledge_provider="test-provider"),
    )

    case_tasks.run_case_sync.run(str(case_id))

    assert sample_delta("alert_sage_case_syncs_total", labels, before) == pytest.approx(1)
