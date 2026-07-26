import json
from dataclasses import dataclass, field

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.knowledge.cases import CaseDocument
from app.integrations.knowledge.dify import (
    DifyAuthenticationError,
    DifyIndexingError,
    DifyKnowledgeAdapter,
    DifyKnowledgeConfig,
    DifyResponseError,
    KnowledgeProviderError,
)
from app.integrations.knowledge.factory import get_knowledge_retriever
from app.integrations.knowledge.retrieval import DocumentChunk
from app.main import app
from app.workflows.alert.adapters import KnowledgeContextProvider

DATASET_ID = "4b193cb1-c70e-4099-b0ee-6d42e0bf57d1"
DOCUMENT_ID = "17de377f-d108-48d9-b59c-05d6447b1863"
SEGMENT_ID = "e8c1bf02-7ca1-4ef0-83a1-a70e852da32e"


def dify_adapter(handler: httpx.MockTransport) -> DifyKnowledgeAdapter:
    return DifyKnowledgeAdapter(
        DifyKnowledgeConfig(
            base_url="https://api.dify.test/v1",
            api_key="dataset-test-secret",
            dataset_id=DATASET_ID,
            http_timeout_seconds=1,
            poll_interval_seconds=0.001,
            max_retries=1,
        ),
        transport=handler,
    )


def refreshing_dify_adapter(handler: httpx.MockTransport) -> DifyKnowledgeAdapter:
    return DifyKnowledgeAdapter(
        DifyKnowledgeConfig(
            base_url="https://api.dify.test/v1",
            api_key="dataset-test-secret",
            dataset_id=DATASET_ID,
            http_timeout_seconds=1,
            poll_interval_seconds=0.001,
            max_retries=1,
            refresh_completed_documents=True,
        ),
        transport=handler,
    )


def case_document() -> CaseDocument:
    from uuid import UUID

    return CaseDocument(
        case_id=UUID("bfead97e-13a5-5ae3-8c12-231555bf6dd3"),
        title="checkout-service CPU 告警处置案例",
        symptom="CPU 使用率持续超过阈值",
        root_cause="慢查询导致工作线程饱和",
        resolution="检查执行计划并回滚异常发布",
        evidence=[{"type": "metric", "source": "prometheus", "score": 0.95}],
        tags=["checkout-service", "HighCPUUsage"],
    )


@pytest.mark.asyncio
async def test_dify_publisher_creates_and_waits_for_indexing() -> None:
    status_reads = 0
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_reads
        assert request.headers["authorization"] == "Bearer dataset-test-secret"
        if request.method == "GET" and request.url.path.endswith("/documents"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path.endswith("/create-by-text"):
            request_bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "document": {
                        "id": DOCUMENT_ID,
                        "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                        "indexing_status": "indexing",
                    },
                    "batch": "batch-001",
                },
            )
        if request.method == "GET" and request.url.path.endswith(f"/documents/{DOCUMENT_ID}"):
            status_reads += 1
            return httpx.Response(
                200,
                json={
                    "id": DOCUMENT_ID,
                    "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                    "indexing_status": "completed" if status_reads > 1 else "indexing",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = dify_adapter(httpx.MockTransport(handler))
    document_id = await adapter.publish(case_document(), idempotency_key="case-sync:test")

    assert document_id == DOCUMENT_ID
    assert status_reads == 2
    assert request_bodies[0]["indexing_technique"] == "high_quality"
    assert request_bodies[0]["process_rule"] == {"mode": "automatic"}
    assert "慢查询导致工作线程饱和" in str(request_bodies[0]["text"])


@pytest.mark.asyncio
async def test_dify_publisher_reuses_completed_document() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["keyword"].startswith("alert-sage-case-")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": DOCUMENT_ID,
                        "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                        "indexing_status": "completed",
                    }
                ]
            },
        )

    adapter = dify_adapter(httpx.MockTransport(handler))
    document_id = await adapter.publish(case_document(), idempotency_key="case-sync:test")

    assert document_id == DOCUMENT_ID
    assert calls == 1


@pytest.mark.asyncio
async def test_dify_evaluation_publisher_refreshes_completed_document() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET" and request.url.path.endswith("/documents"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": DOCUMENT_ID,
                            "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                            "indexing_status": "completed",
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path.endswith("/update-by-text"):
            body = json.loads(request.content)
            assert body["name"].startswith("alert-sage-case-")
            return httpx.Response(
                200,
                json={
                    "document": {
                        "id": DOCUMENT_ID,
                        "name": body["name"],
                        "indexing_status": "waiting",
                    },
                    "batch": "batch-refresh-001",
                },
            )
        if request.method == "GET" and request.url.path.endswith(f"/documents/{DOCUMENT_ID}"):
            return httpx.Response(
                200,
                json={
                    "id": DOCUMENT_ID,
                    "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                    "indexing_status": "completed",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = refreshing_dify_adapter(httpx.MockTransport(handler))
    document_id = await adapter.publish(case_document(), idempotency_key="evaluation:refresh")

    assert document_id == DOCUMENT_ID
    assert methods == ["GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_dify_publisher_updates_a_failed_document_before_retrying() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET" and request.url.path.endswith("/documents"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": DOCUMENT_ID,
                            "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                            "indexing_status": "error",
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path.endswith("/update-by-text"):
            return httpx.Response(
                200,
                json={
                    "document": {
                        "id": DOCUMENT_ID,
                        "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                        "indexing_status": "waiting",
                    },
                    "batch": "batch-retry-001",
                },
            )
        if request.method == "GET" and request.url.path.endswith(f"/documents/{DOCUMENT_ID}"):
            return httpx.Response(
                200,
                json={
                    "id": DOCUMENT_ID,
                    "name": "alert-sage-case-bfead97e-13a5-5ae3-8c12-231555bf6dd3.md",
                    "indexing_status": "completed",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = dify_adapter(httpx.MockTransport(handler))

    assert await adapter.publish(case_document(), idempotency_key="case-sync:test") == DOCUMENT_ID
    assert methods == ["GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_dify_publisher_reports_terminal_indexing_failure_without_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents"):
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/create-by-text"):
            return httpx.Response(
                200,
                json={
                    "document": {
                        "id": DOCUMENT_ID,
                        "name": "alert-sage-case.md",
                        "indexing_status": "waiting",
                    },
                    "batch": "batch-failed-001",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": DOCUMENT_ID,
                "name": "alert-sage-case.md",
                "indexing_status": "error",
                "error": "dataset-test-secret internal provider detail",
            },
        )

    adapter = dify_adapter(httpx.MockTransport(handler))
    with pytest.raises(DifyIndexingError) as captured:
        await adapter.publish(case_document(), idempotency_key="case-sync:test")

    assert "dataset-test-secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_dify_retriever_normalizes_chunks_and_retries_transient_failure() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        body = json.loads(request.content)
        assert body["query"] == "checkout-service CPU"
        assert body["retrieval_model"]["top_k"] == 3
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "segment": {
                            "id": SEGMENT_ID,
                            "document_id": DOCUMENT_ID,
                            "content": "检查慢查询并回滚异常发布。",
                            "document": {
                                "id": DOCUMENT_ID,
                                "name": "alert-sage-case.md",
                                "doc_metadata": [{"name": "source", "value": "alert-sage"}],
                            },
                        },
                        "score": 0.91,
                    }
                ]
            },
        )

    adapter = dify_adapter(httpx.MockTransport(handler))
    chunks = await adapter.retrieve("checkout-service CPU", top_k=3, score_threshold=0.5)

    assert attempts == 2
    assert chunks == [
        DocumentChunk(
            id=SEGMENT_ID,
            document_id=DOCUMENT_ID,
            document_name="alert-sage-case.md",
            content="检查慢查询并回滚异常发布。",
            score=0.91,
            source=(f"dify://datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments/{SEGMENT_ID}"),
            metadata={"fields": [{"name": "source", "value": "alert-sage"}]},
        )
    ]


@pytest.mark.asyncio
async def test_dify_errors_do_not_expose_credentials_or_response_bodies() -> None:
    def auth_failure(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "dataset-test-secret is invalid"})

    adapter = dify_adapter(httpx.MockTransport(auth_failure))
    with pytest.raises(DifyAuthenticationError) as captured:
        await adapter.retrieve("cpu", top_k=3)
    assert "dataset-test-secret" not in str(captured.value)

    def invalid_response(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"records": [{"unexpected": True}]})

    invalid_adapter = dify_adapter(httpx.MockTransport(invalid_response))
    with pytest.raises(DifyResponseError, match="schema validation"):
        await invalid_adapter.retrieve("cpu", top_k=3)


def test_dify_settings_require_secret_and_valid_dataset_id() -> None:
    with pytest.raises(ValidationError, match="DIFY_API_KEY"):
        Settings(
            _env_file=None,
            knowledge_provider="dify",
            dify_dataset_id=DATASET_ID,
        )
    with pytest.raises(ValidationError, match="must be a UUID"):
        Settings(
            _env_file=None,
            knowledge_provider="dify",
            dify_api_key="dataset-secret",
            dify_dataset_id="not-a-uuid",
        )
    settings = Settings(
        _env_file=None,
        knowledge_provider="dify",
        dify_api_key="dataset-secret",
        dify_dataset_id=DATASET_ID,
    )
    assert "dataset-secret" not in repr(settings)


def test_dify_settings_validate_evaluation_dataset_id() -> None:
    with pytest.raises(ValidationError, match="DIFY_EVALUATION_DATASET_ID must be a UUID"):
        Settings(
            _env_file=None,
            knowledge_provider="dify",
            dify_api_key="dataset-secret",
            dify_dataset_id=DATASET_ID,
            dify_evaluation_dataset_id="not-a-uuid",
        )


@dataclass
class RecordingRetriever:
    queries: list[str] = field(default_factory=list)
    fail: bool = False

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[DocumentChunk]:
        del score_threshold
        self.queries.append(query)
        if self.fail:
            raise KnowledgeProviderError("provider failed")
        return [
            DocumentChunk(
                id="chunk-1",
                document_id="document-1",
                document_name="case.md",
                content="historical case content",
                score=0.9,
                source="dify://case/chunk-1",
                metadata={},
            )
        ][:top_k]


@pytest.mark.asyncio
async def test_workflow_knowledge_context_uses_normalized_retriever() -> None:
    retriever = RecordingRetriever()
    result = await KnowledgeContextProvider(retriever).collect(
        {
            "service": "checkout-service",
            "alert_name": "HighCPUUsage",
            "payload": {"summary": "CPU exceeded 80 percent"},
        }
    )

    assert retriever.queries == ["checkout-service HighCPUUsage CPU exceeded 80 percent"]
    assert result.source_refs == ["dify://case/chunk-1"]
    assert result.data["matches"][0]["document_id"] == "document-1"


@pytest.mark.asyncio
async def test_knowledge_search_api_returns_sources() -> None:
    retriever = RecordingRetriever()
    app.dependency_overrides[get_knowledge_retriever] = lambda: retriever
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/knowledge/search",
                json={"query": "checkout CPU", "top_k": 3},
            )
    finally:
        app.dependency_overrides.pop(get_knowledge_retriever, None)

    assert response.status_code == 200
    assert response.json()["items"][0]["source"] == "dify://case/chunk-1"


@pytest.mark.asyncio
async def test_knowledge_search_api_hides_provider_failure() -> None:
    retriever = RecordingRetriever(fail=True)
    app.dependency_overrides[get_knowledge_retriever] = lambda: retriever
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/knowledge/search",
                json={"query": "checkout CPU"},
            )
    finally:
        app.dependency_overrides.pop(get_knowledge_retriever, None)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "knowledge_provider_unavailable",
            "message": "knowledge retrieval is temporarily unavailable",
            "context": {"provider": "mock"},
        }
    }


@pytest.mark.asyncio
async def test_knowledge_search_api_rejects_blank_queries() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/knowledge/search",
            json={"query": "   "},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
