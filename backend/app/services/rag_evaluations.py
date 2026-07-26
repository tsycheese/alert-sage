from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.evaluation.loader import EvaluationSetLoadError, LoadedEvaluationSet, load_evaluation_set
from app.evaluation.metrics import evaluate_retrieval
from app.evaluation.schemas import (
    EvaluationQuery,
    RagEvaluationReport,
    RetrievalObservation,
)
from app.integrations.knowledge.dify import KnowledgeProviderError
from app.integrations.knowledge.retrieval import DocumentChunk, KnowledgeRetriever
from app.models.enums import OutboxTopic, RagEvaluationRunStatus, RagEvaluationSplit
from app.models.rag_evaluation import RagEvaluationResult, RagEvaluationRun
from app.services.outbox import enqueue_outbox_message


class RagEvaluationNotFoundError(Exception):
    pass


class RagEvaluationIdempotencyConflictError(Exception):
    pass


class RagEvaluationConfigurationError(Exception):
    pass


class RagEvaluationExecutionError(Exception):
    def __init__(self, run_id: UUID, cause: Exception) -> None:
        super().__init__(f"RAG evaluation run {run_id} failed")
        self.run_id = run_id
        self.cause = cause


@dataclass(frozen=True, slots=True)
class PreparedRagEvaluationRun:
    run: RagEvaluationRun
    delivery_created: bool


@dataclass(frozen=True, slots=True)
class RagEvaluationRunPage:
    items: list[RagEvaluationRun]
    total: int
    page: int
    page_size: int
    pages: int


@dataclass(frozen=True, slots=True)
class RetrievedQueryData:
    observation: RetrievalObservation
    items: list[dict[str, object]]


class RagEvaluationQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, run_id: UUID) -> RagEvaluationRun:
        run = await self.session.scalar(
            select(RagEvaluationRun)
            .options(selectinload(RagEvaluationRun.results))
            .where(RagEvaluationRun.id == run_id)
        )
        if run is None:
            raise RagEvaluationNotFoundError
        return run

    async def list(self, *, page: int, page_size: int) -> RagEvaluationRunPage:
        total = int(await self.session.scalar(select(func.count(RagEvaluationRun.id))) or 0)
        items = list(
            await self.session.scalars(
                select(RagEvaluationRun)
                .order_by(RagEvaluationRun.created_at.desc(), RagEvaluationRun.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return RagEvaluationRunPage(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=(total + page_size - 1) // page_size,
        )


class RagEvaluationService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        evaluation_set_path: str | Path,
        provider: str,
        dataset_id: str | None,
        build_revision: str | None,
        retrieval_timeout_seconds: float,
        query_interval_seconds: float = 0,
    ) -> None:
        self.session_factory = session_factory
        self.evaluation_set_path = Path(evaluation_set_path)
        self.provider = provider
        self.dataset_id = dataset_id.strip() if dataset_id else None
        self.build_revision = build_revision.strip()[:64] if build_revision else None
        self.retrieval_timeout_seconds = retrieval_timeout_seconds
        self.query_interval_seconds = query_interval_seconds

    async def prepare_run(
        self,
        *,
        idempotency_key: str,
        split: RagEvaluationSplit,
        top_k: int,
        score_threshold: float | None,
    ) -> PreparedRagEvaluationRun:
        loaded = self._load_set()
        if self.provider == "dify":
            if self.dataset_id is None:
                raise RagEvaluationConfigurationError("Dify evaluation dataset is not configured")
            try:
                UUID(self.dataset_id)
            except ValueError as exc:
                raise RagEvaluationConfigurationError(
                    "Dify evaluation dataset id must be a UUID"
                ) from exc
        query_count = sum(query.split == split for query in loaded.evaluation_set.queries)
        expected: dict[str, object] = {
            "evaluation_set_id": loaded.evaluation_set.id,
            "evaluation_set_version": loaded.evaluation_set.version,
            "evaluation_set_sha256": loaded.sha256,
            "provider": self.provider,
            "dataset_id": self.dataset_id,
            "split": split,
            "top_k": top_k,
            "score_threshold": score_threshold,
            "query_count": query_count,
        }
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(RagEvaluationRun).where(RagEvaluationRun.idempotency_key == idempotency_key)
            )
            if existing is not None:
                self._ensure_same_run(existing, expected)
                return PreparedRagEvaluationRun(existing, False)

            run = RagEvaluationRun(
                idempotency_key=idempotency_key,
                build_revision=self.build_revision,
                **expected,
            )
            session.add(run)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(RagEvaluationRun).where(
                        RagEvaluationRun.idempotency_key == idempotency_key
                    )
                )
                if existing is None:
                    raise
                self._ensure_same_run(existing, expected)
                return PreparedRagEvaluationRun(existing, False)

            _, delivery_created = await enqueue_outbox_message(
                session,
                topic=OutboxTopic.RAG_EVALUATION_RUN,
                aggregate_id=run.id,
                idempotency_key=f"rag-evaluation:run:{run.id}",
                payload={"rag_evaluation_run_id": str(run.id)},
                correlation={"rag_evaluation_run_id": run.id},
            )
            await session.commit()
            return PreparedRagEvaluationRun(run, delivery_created)

    async def execute(
        self,
        run_id: UUID,
        *,
        retriever: KnowledgeRetriever,
    ) -> RagEvaluationRun:
        try:
            run = await self._mark_running(run_id)
            if run.status == RagEvaluationRunStatus.COMPLETED:
                return run
            loaded = self._load_set()
            self._verify_loaded_set(run, loaded)
            split = RagEvaluationSplit(str(run.split))
            queries = [query for query in loaded.evaluation_set.queries if query.split == split]
            retrieved = await self._retrieve_queries(
                queries,
                loaded=loaded,
                retriever=retriever,
                top_k=run.top_k,
                score_threshold=run.score_threshold,
            )
            report = evaluate_retrieval(
                loaded.evaluation_set,
                [item.observation for item in retrieved],
                top_k=run.top_k,
                split=split,
            )
            await self._persist_completed(run_id, queries, retrieved, report)
            return await self.get(run_id)
        except RagEvaluationNotFoundError:
            raise
        except Exception as exc:
            await self._mark_failed(run_id, exc)
            raise RagEvaluationExecutionError(run_id, exc) from exc

    async def get(self, run_id: UUID) -> RagEvaluationRun:
        async with self.session_factory() as session:
            return await RagEvaluationQueryService(session).get(run_id)

    async def _mark_running(self, run_id: UUID) -> RagEvaluationRun:
        async with self.session_factory() as session:
            run = await session.scalar(
                select(RagEvaluationRun).where(RagEvaluationRun.id == run_id).with_for_update()
            )
            if run is None:
                raise RagEvaluationNotFoundError
            if run.status == RagEvaluationRunStatus.COMPLETED:
                return run
            await session.execute(
                delete(RagEvaluationResult).where(RagEvaluationResult.run_id == run.id)
            )
            run.status = RagEvaluationRunStatus.RUNNING
            run.attempt += 1
            run.completed_query_count = 0
            run.summary_metrics = None
            run.error_code = None
            run.error_message = None
            run.started_at = datetime.now(UTC)
            run.finished_at = None
            await session.commit()
            return run

    async def _retrieve_query(
        self,
        query: EvaluationQuery,
        *,
        loaded: LoadedEvaluationSet,
        retriever: KnowledgeRetriever,
        top_k: int,
        score_threshold: float | None,
    ) -> RetrievedQueryData:
        started = monotonic()
        chunks: list[DocumentChunk] = []
        error_code: str | None = None
        try:
            chunks = await asyncio.wait_for(
                retriever.retrieve(
                    query.query,
                    top_k=top_k,
                    score_threshold=score_threshold,
                ),
                timeout=self.retrieval_timeout_seconds,
            )
        except TimeoutError:
            error_code = "retrieval-timeout"
        except KnowledgeProviderError:
            error_code = "knowledge-provider-error"

        expected_names = {
            f"alert-sage-case-{document.case_id}.md": document.case_id
            for document in loaded.evaluation_set.documents
        }
        items: list[dict[str, object]] = []
        retrieved_case_ids: list[UUID] = []
        for rank, chunk in enumerate(chunks, start=1):
            case_id = expected_names.get(chunk.document_name)
            if case_id is not None:
                retrieved_case_ids.append(case_id)
            items.append(
                {
                    "rank": rank,
                    "case_id": str(case_id) if case_id is not None else None,
                    "document_id": chunk.document_id,
                    "document_name": chunk.document_name,
                    "score": chunk.score,
                    "source": chunk.source,
                }
            )
        return RetrievedQueryData(
            observation=RetrievalObservation(
                query_id=query.id,
                retrieved_case_ids=retrieved_case_ids,
                latency_ms=max(0.0, monotonic() - started) * 1000,
                error_code=error_code,
            ),
            items=items,
        )

    async def _retrieve_queries(
        self,
        queries: list[EvaluationQuery],
        *,
        loaded: LoadedEvaluationSet,
        retriever: KnowledgeRetriever,
        top_k: int,
        score_threshold: float | None,
    ) -> list[RetrievedQueryData]:
        retrieved: list[RetrievedQueryData] = []
        for position, query in enumerate(queries):
            retrieved.append(
                await self._retrieve_query(
                    query,
                    loaded=loaded,
                    retriever=retriever,
                    top_k=top_k,
                    score_threshold=score_threshold,
                )
            )
            if position < len(queries) - 1 and self.query_interval_seconds > 0:
                await asyncio.sleep(self.query_interval_seconds)
        return retrieved

    async def _persist_completed(
        self,
        run_id: UUID,
        queries: list[EvaluationQuery],
        retrieved: list[RetrievedQueryData],
        report: RagEvaluationReport,
    ) -> None:
        metric_by_query = {item.query_id: item for item in report.queries}
        retrieved_by_query = {item.observation.query_id: item for item in retrieved}
        async with self.session_factory() as session:
            run = await session.scalar(
                select(RagEvaluationRun).where(RagEvaluationRun.id == run_id).with_for_update()
            )
            if run is None:
                raise RagEvaluationNotFoundError
            for query in queries:
                metric = metric_by_query[query.id]
                query_data = retrieved_by_query[query.id]
                session.add(
                    RagEvaluationResult(
                        run_id=run.id,
                        query_id=query.id,
                        split=query.split,
                        difficulty=query.difficulty,
                        query_text=query.query,
                        ground_truth={
                            "relevant_case_ids": [
                                str(case_id) for case_id in query.relevant_case_ids
                            ],
                            "expected_answer_points": query.expected_answer_points,
                            "tags": query.tags,
                            "should_abstain": query.should_abstain,
                        },
                        retrieved_items=query_data.items,
                        source_hit_at_k=metric.source_hit_at_k,
                        recall_at_k=metric.recall_at_k,
                        reciprocal_rank=metric.reciprocal_rank,
                        abstention_correct=metric.abstention_correct,
                        false_positive=metric.false_positive,
                        latency_ms=metric.latency_ms,
                        error_code=query_data.observation.error_code,
                    )
                )
            run.status = RagEvaluationRunStatus.COMPLETED
            run.completed_query_count = len(queries)
            run.summary_metrics = {
                "top_k": report.top_k,
                "overall": report.overall.model_dump(mode="json"),
                "by_split": {
                    key: value.model_dump(mode="json") for key, value in report.by_split.items()
                },
            }
            run.finished_at = datetime.now(UTC)
            await session.commit()

    async def _mark_failed(self, run_id: UUID, error: Exception) -> None:
        async with self.session_factory() as session:
            run = await session.scalar(
                select(RagEvaluationRun).where(RagEvaluationRun.id == run_id).with_for_update()
            )
            if run is None or run.status == RagEvaluationRunStatus.COMPLETED:
                return
            run.status = RagEvaluationRunStatus.FAILED
            run.error_code = self._execution_error_code(error)
            run.error_message = "RAG evaluation execution failed"
            run.finished_at = datetime.now(UTC)
            await session.commit()

    def _load_set(self) -> LoadedEvaluationSet:
        try:
            return load_evaluation_set(self.evaluation_set_path)
        except EvaluationSetLoadError as exc:
            raise RagEvaluationConfigurationError("RAG evaluation set is unavailable") from exc

    @staticmethod
    def _verify_loaded_set(run: RagEvaluationRun, loaded: LoadedEvaluationSet) -> None:
        if (
            run.evaluation_set_id != loaded.evaluation_set.id
            or run.evaluation_set_version != loaded.evaluation_set.version
            or run.evaluation_set_sha256 != loaded.sha256
        ):
            raise RagEvaluationConfigurationError("RAG evaluation set changed after scheduling")

    @staticmethod
    def _ensure_same_run(run: RagEvaluationRun, expected: dict[str, object]) -> None:
        for field, value in expected.items():
            if getattr(run, field) != value:
                raise RagEvaluationIdempotencyConflictError(
                    "RAG evaluation idempotency key has conflicting content"
                )

    @staticmethod
    def _execution_error_code(error: Exception) -> str:
        if isinstance(error, RagEvaluationConfigurationError):
            return "evaluation-configuration-error"
        return type(error).__name__[:64]
