from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.routes import rag_evaluations as evaluation_routes
from app.evaluation.loader import load_evaluation_set
from app.integrations.knowledge.dify import KnowledgeProviderError
from app.integrations.knowledge.retrieval import DocumentChunk
from app.models.enums import OutboxTopic, RagEvaluationRunStatus, RagEvaluationSplit
from app.models.outbox import OutboxMessage
from app.models.rag_evaluation import RagEvaluationResult, RagEvaluationRun
from app.schemas.rag_evaluation import RagEvaluationRunCreate
from app.services import rag_evaluations as evaluation_services
from app.services.rag_evaluations import (
    RagEvaluationExecutionError,
    RagEvaluationIdempotencyConflictError,
    RagEvaluationService,
)

EVALUATION_SET_PATH = Path(__file__).parents[1] / "evaluation_sets" / "rag-v2.6-baseline.json"
EVALUATION_DATASET_ID = "70000000-0000-4000-8000-000000000007"


class PerfectRetriever:
    def __init__(self) -> None:
        evaluation_set = load_evaluation_set(EVALUATION_SET_PATH).evaluation_set
        self._documents = {document.case_id: document for document in evaluation_set.documents}
        self._queries = {query.query: query for query in evaluation_set.queries}

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[DocumentChunk]:
        del score_threshold
        evaluation_query = self._queries[query]
        return [
            DocumentChunk(
                id=f"chunk-{case_id}",
                document_id=f"document-{case_id}",
                document_name=f"alert-sage-case-{case_id}.md",
                content="content must not be persisted in evaluation results",
                score=0.99 - (rank * 0.01),
                source=f"dify://documents/{case_id}/segments/{rank}",
                metadata={},
            )
            for rank, case_id in enumerate(evaluation_query.relevant_case_ids[:top_k])
            if case_id in self._documents
        ]


class FailingRetriever:
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[DocumentChunk]:
        del query, top_k, score_threshold
        raise KnowledgeProviderError("provider details must stay private")


def service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    evaluation_set_path: Path = EVALUATION_SET_PATH,
) -> RagEvaluationService:
    return RagEvaluationService(
        session_factory=session_factory,
        evaluation_set_path=evaluation_set_path,
        provider="dify",
        dataset_id=EVALUATION_DATASET_ID,
        build_revision="test-revision",
        retrieval_timeout_seconds=0.1,
    )


def test_test_split_requires_explicit_confirmation() -> None:
    with pytest.raises(ValidationError, match="confirm_test_set=true"):
        RagEvaluationRunCreate(
            idempotency_key="evaluation-test-run",
            split=RagEvaluationSplit.TEST,
        )

    command = RagEvaluationRunCreate(
        idempotency_key="evaluation-test-run",
        split=RagEvaluationSplit.TEST,
        confirm_test_set=True,
    )
    assert command.split is RagEvaluationSplit.TEST


@pytest.mark.asyncio
async def test_prepare_run_is_atomic_and_idempotent(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    evaluation_service = service(test_session_factory)
    prepared = await evaluation_service.prepare_run(
        idempotency_key="evaluation-calibration-001",
        split=RagEvaluationSplit.CALIBRATION,
        top_k=3,
        score_threshold=0.4,
    )

    assert prepared.delivery_created is True
    assert prepared.run.status == RagEvaluationRunStatus.QUEUED
    assert prepared.run.query_count == 12
    async with test_session_factory() as session:
        message = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == prepared.run.id,
                OutboxMessage.topic == OutboxTopic.RAG_EVALUATION_RUN,
            )
        )
    assert message is not None
    assert message.payload == {"rag_evaluation_run_id": str(prepared.run.id)}
    assert message.correlation["rag_evaluation_run_id"] == str(prepared.run.id)

    replay = await evaluation_service.prepare_run(
        idempotency_key="evaluation-calibration-001",
        split=RagEvaluationSplit.CALIBRATION,
        top_k=3,
        score_threshold=0.4,
    )
    assert replay.run.id == prepared.run.id
    assert replay.delivery_created is False

    with pytest.raises(RagEvaluationIdempotencyConflictError):
        await evaluation_service.prepare_run(
            idempotency_key="evaluation-calibration-001",
            split=RagEvaluationSplit.CALIBRATION,
            top_k=5,
            score_threshold=0.4,
        )


@pytest.mark.asyncio
async def test_prepare_run_rolls_back_when_outbox_write_fails(
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_enqueue(*args: object, **kwargs: object) -> tuple[object, bool]:
        del args, kwargs
        raise RuntimeError("simulated durable delivery failure")

    monkeypatch.setattr(evaluation_services, "enqueue_outbox_message", fail_enqueue)
    with pytest.raises(RuntimeError, match="durable delivery failure"):
        await service(test_session_factory).prepare_run(
            idempotency_key="evaluation-atomicity-001",
            split=RagEvaluationSplit.CALIBRATION,
            top_k=3,
            score_threshold=None,
        )

    async with test_session_factory() as session:
        run_count = await session.scalar(select(func.count()).select_from(RagEvaluationRun))
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxMessage))
    assert run_count == 0
    assert outbox_count == 0


@pytest.mark.asyncio
async def test_execute_persists_metrics_without_chunk_content_and_is_idempotent(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    evaluation_service = service(test_session_factory)
    prepared = await evaluation_service.prepare_run(
        idempotency_key="evaluation-execute-001",
        split=RagEvaluationSplit.CALIBRATION,
        top_k=3,
        score_threshold=None,
    )

    completed = await evaluation_service.execute(prepared.run.id, retriever=PerfectRetriever())
    assert completed.status == RagEvaluationRunStatus.COMPLETED
    assert completed.attempt == 1
    assert completed.completed_query_count == completed.query_count == 12
    assert completed.summary_metrics is not None
    assert completed.summary_metrics["overall"]["source_hit_rate_at_k"] == 1.0
    assert completed.summary_metrics["overall"]["error_rate"] == 0.0
    assert len(completed.results) == 12
    serialized_items = str([item.retrieved_items for item in completed.results])
    assert "content must not be persisted" not in serialized_items

    replay = await evaluation_service.execute(prepared.run.id, retriever=FailingRetriever())
    assert replay.status == RagEvaluationRunStatus.COMPLETED
    assert replay.attempt == 1
    async with test_session_factory() as session:
        result_count = await session.scalar(
            select(func.count())
            .select_from(RagEvaluationResult)
            .where(RagEvaluationResult.run_id == prepared.run.id)
        )
    assert result_count == 12


@pytest.mark.asyncio
async def test_provider_failures_are_recorded_per_query_without_leaking_details(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    evaluation_service = service(test_session_factory)
    prepared = await evaluation_service.prepare_run(
        idempotency_key="evaluation-provider-failure-001",
        split=RagEvaluationSplit.CALIBRATION,
        top_k=3,
        score_threshold=None,
    )

    completed = await evaluation_service.execute(prepared.run.id, retriever=FailingRetriever())
    assert completed.status == RagEvaluationRunStatus.COMPLETED
    assert completed.summary_metrics is not None
    assert completed.summary_metrics["overall"]["error_rate"] == 1.0
    assert {result.error_code for result in completed.results} == {"knowledge-provider-error"}
    assert "provider details" not in str(completed.summary_metrics)
    assert "provider details" not in str([result.retrieved_items for result in completed.results])


@pytest.mark.asyncio
async def test_evaluation_queries_are_paced_without_sleeping_after_last_query(
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(evaluation_services.asyncio, "sleep", record_sleep)
    evaluation_service = RagEvaluationService(
        session_factory=test_session_factory,
        evaluation_set_path=EVALUATION_SET_PATH,
        provider="dify",
        dataset_id=EVALUATION_DATASET_ID,
        build_revision=None,
        retrieval_timeout_seconds=1,
        query_interval_seconds=0.5,
    )
    prepared = await evaluation_service.prepare_run(
        idempotency_key="evaluation-paced-001",
        split=RagEvaluationSplit.CALIBRATION,
        top_k=3,
        score_threshold=None,
    )

    completed = await evaluation_service.execute(prepared.run.id, retriever=FailingRetriever())

    assert completed.status == RagEvaluationRunStatus.COMPLETED
    assert sleeps == [0.5] * 11


@pytest.mark.asyncio
async def test_changed_evaluation_set_marks_run_failed(
    test_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    mutable_set = tmp_path / "evaluation.json"
    mutable_set.write_bytes(EVALUATION_SET_PATH.read_bytes())
    evaluation_service = service(test_session_factory, evaluation_set_path=mutable_set)
    prepared = await evaluation_service.prepare_run(
        idempotency_key="evaluation-mutated-set-001",
        split=RagEvaluationSplit.CALIBRATION,
        top_k=3,
        score_threshold=None,
    )
    mutable_set.write_bytes(mutable_set.read_bytes() + b"\n")

    with pytest.raises(RagEvaluationExecutionError):
        await evaluation_service.execute(prepared.run.id, retriever=PerfectRetriever())

    failed = await evaluation_service.get(prepared.run.id)
    assert failed.status == RagEvaluationRunStatus.FAILED
    assert failed.error_code == "evaluation-configuration-error"
    assert failed.error_message == "RAG evaluation execution failed"


@pytest.mark.asyncio
async def test_slow_retrieval_is_recorded_as_timeout(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class SlowRetriever:
        async def retrieve(
            self,
            query: str,
            *,
            top_k: int,
            score_threshold: float | None = None,
        ) -> list[DocumentChunk]:
            del query, top_k, score_threshold
            await asyncio.sleep(0.02)
            return []

    evaluation_service = RagEvaluationService(
        session_factory=test_session_factory,
        evaluation_set_path=EVALUATION_SET_PATH,
        provider="dify",
        dataset_id=EVALUATION_DATASET_ID,
        build_revision=None,
        retrieval_timeout_seconds=0.001,
    )
    prepared = await evaluation_service.prepare_run(
        idempotency_key="evaluation-timeout-001",
        split=RagEvaluationSplit.TEST,
        top_k=3,
        score_threshold=None,
    )
    completed = await evaluation_service.execute(prepared.run.id, retriever=SlowRetriever())
    assert completed.status == RagEvaluationRunStatus.COMPLETED
    assert {result.error_code for result in completed.results} == {"retrieval-timeout"}


@pytest.mark.asyncio
async def test_rag_evaluation_api_create_replay_list_and_detail(
    api_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evaluation_routes,
        "get_settings",
        lambda: SimpleNamespace(
            rag_evaluation_set_path=str(EVALUATION_SET_PATH),
            knowledge_provider="mock",
            dify_evaluation_dataset_id=None,
            build_revision="api-test",
            knowledge_retrieval_timeout_seconds=1,
        ),
    )
    client = api_client
    create = await client.post(  # type: ignore[attr-defined]
        "/api/v1/rag/evaluations/runs",
        json={
            "idempotency_key": "evaluation-api-001",
            "split": "calibration",
            "top_k": 3,
        },
    )
    assert create.status_code == 202
    assert create.headers["X-Idempotent-Replay"] == "false"
    payload = create.json()
    run_id = payload["run"]["id"]
    assert payload["dispatched"] is True

    replay = await client.post(  # type: ignore[attr-defined]
        "/api/v1/rag/evaluations/runs",
        json={
            "idempotency_key": "evaluation-api-001",
            "split": "calibration",
            "top_k": 3,
        },
    )
    assert replay.status_code == 200
    assert replay.headers["X-Idempotent-Replay"] == "true"
    assert replay.json()["run"]["id"] == run_id

    listing = await client.get("/api/v1/rag/evaluations/runs")  # type: ignore[attr-defined]
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    queued = await client.get(  # type: ignore[attr-defined]
        "/api/v1/rag/evaluations/runs?split=calibration&status=queued"
    )
    assert queued.status_code == 200
    assert queued.json()["total"] == 1
    test_runs = await client.get(  # type: ignore[attr-defined]
        "/api/v1/rag/evaluations/runs?split=test"
    )
    assert test_runs.status_code == 200
    assert test_runs.json()["total"] == 0
    detail = await client.get(  # type: ignore[attr-defined]
        f"/api/v1/rag/evaluations/runs/{run_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["results"] == []

    missing = await client.get(  # type: ignore[attr-defined]
        f"/api/v1/rag/evaluations/runs/{uuid4()}"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "rag_evaluation_not_found"

    rejected_test = await client.post(  # type: ignore[attr-defined]
        "/api/v1/rag/evaluations/runs",
        json={
            "idempotency_key": "evaluation-api-test-001",
            "split": "test",
        },
    )
    assert rejected_test.status_code == 422


def test_rag_evaluation_ids_are_valid_uuids() -> None:
    assert UUID(EVALUATION_DATASET_ID)
