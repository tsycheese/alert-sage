from __future__ import annotations

import math
from collections.abc import Iterable
from statistics import fmean
from uuid import UUID

from app.evaluation.schemas import (
    AggregateEvaluationMetrics,
    EvaluationQuery,
    QueryEvaluationMetrics,
    RagEvaluationReport,
    RagEvaluationSet,
    RetrievalObservation,
)


class EvaluationObservationError(ValueError):
    """Raised when observations do not match the selected evaluation set."""


def _deduplicate(values: Iterable[UUID]) -> list[UUID]:
    return list(dict.fromkeys(values))


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _mean_or_none(values: Iterable[int | float]) -> float | None:
    materialized = list(values)
    return fmean(materialized) if materialized else None


def _score_query(
    query: EvaluationQuery,
    observation: RetrievalObservation,
    *,
    top_k: int,
) -> QueryEvaluationMetrics:
    retrieved = _deduplicate(observation.retrieved_case_ids)[:top_k]
    failed = observation.error_code is not None
    if query.should_abstain:
        return QueryEvaluationMetrics(
            query_id=query.id,
            split=query.split,
            difficulty=query.difficulty,
            answerable=False,
            source_hit_at_k=None,
            recall_at_k=None,
            reciprocal_rank=None,
            abstention_correct=not failed and not retrieved,
            false_positive=bool(retrieved) if not failed else None,
            error=failed,
            latency_ms=observation.latency_ms,
        )

    relevant = set(query.relevant_case_ids)
    matched = relevant.intersection(retrieved)
    first_rank = next(
        (rank for rank, case_id in enumerate(retrieved, start=1) if case_id in relevant),
        None,
    )
    return QueryEvaluationMetrics(
        query_id=query.id,
        split=query.split,
        difficulty=query.difficulty,
        answerable=True,
        source_hit_at_k=first_rank is not None,
        recall_at_k=len(matched) / len(relevant),
        reciprocal_rank=0.0 if first_rank is None else 1 / first_rank,
        abstention_correct=None,
        false_positive=None,
        error=failed,
        latency_ms=observation.latency_ms,
    )


def _aggregate(items: list[QueryEvaluationMetrics]) -> AggregateEvaluationMetrics:
    answerable = [item for item in items if item.answerable]
    abstention = [item for item in items if not item.answerable]
    successful_abstention = [item for item in abstention if not item.error]
    latencies = [item.latency_ms for item in items]
    error_count = sum(item.error for item in items)
    return AggregateEvaluationMetrics(
        query_count=len(items),
        answerable_count=len(answerable),
        abstention_count=len(abstention),
        error_count=error_count,
        source_hit_rate_at_k=_mean_or_none(
            int(item.source_hit_at_k is True) for item in answerable
        ),
        recall_at_k=_mean_or_none(
            item.recall_at_k for item in answerable if item.recall_at_k is not None
        ),
        mean_reciprocal_rank=_mean_or_none(
            item.reciprocal_rank for item in answerable if item.reciprocal_rank is not None
        ),
        abstention_accuracy=_mean_or_none(
            int(item.abstention_correct is True) for item in abstention
        ),
        no_answer_false_positive_rate=_mean_or_none(
            int(item.false_positive is True) for item in successful_abstention
        ),
        error_rate=error_count / len(items) if items else 0.0,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
    )


def evaluate_retrieval(
    evaluation_set: RagEvaluationSet,
    observations: list[RetrievalObservation],
    *,
    top_k: int,
) -> RagEvaluationReport:
    if top_k < 1 or top_k > 10:
        raise ValueError("top_k must be between 1 and 10")

    observation_by_query: dict[str, RetrievalObservation] = {}
    for observation in observations:
        if observation.query_id in observation_by_query:
            raise EvaluationObservationError(
                f"duplicate observation for query: {observation.query_id}"
            )
        observation_by_query[observation.query_id] = observation

    query_ids = {query.id for query in evaluation_set.queries}
    observed_ids = set(observation_by_query)
    missing = query_ids - observed_ids
    unknown = observed_ids - query_ids
    if missing or unknown:
        parts: list[str] = []
        if missing:
            parts.append("missing=" + ",".join(sorted(missing)))
        if unknown:
            parts.append("unknown=" + ",".join(sorted(unknown)))
        raise EvaluationObservationError("observation coverage mismatch: " + "; ".join(parts))

    query_metrics = [
        _score_query(query, observation_by_query[query.id], top_k=top_k)
        for query in evaluation_set.queries
    ]
    return RagEvaluationReport(
        evaluation_set_id=evaluation_set.id,
        evaluation_set_version=evaluation_set.version,
        top_k=top_k,
        overall=_aggregate(query_metrics),
        by_split={
            split: _aggregate([item for item in query_metrics if item.split == split])
            for split in ("calibration", "test")
        },
        queries=query_metrics,
    )
