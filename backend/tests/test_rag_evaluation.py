from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.evaluation.loader import EvaluationSetLoadError, load_evaluation_set
from app.evaluation.metrics import EvaluationObservationError, evaluate_retrieval
from app.evaluation.schemas import (
    EvaluationDocument,
    EvaluationQuery,
    RagEvaluationSet,
    RetrievalObservation,
)

CASE_A = UUID("11111111-1111-4111-8111-111111111111")
CASE_B = UUID("22222222-2222-4222-8222-222222222222")
CASE_C = UUID("33333333-3333-4333-8333-333333333333")
BASELINE_PATH = Path(__file__).parents[1] / "evaluation_sets" / "rag-v2.6-baseline.json"


def document(key: str, case_id: UUID) -> EvaluationDocument:
    return EvaluationDocument(
        key=key,
        case_id=case_id,
        title=f"{key} title",
        symptom="symptom",
        root_cause="root cause",
        resolution="resolution",
        tags=["operations"],
    )


def evaluation_set() -> RagEvaluationSet:
    return RagEvaluationSet(
        id="test-evaluation-set",
        version="1.0.0",
        name="Test evaluation set",
        description="Small deterministic fixture",
        documents=[document("case-alpha", CASE_A), document("case-beta", CASE_B)],
        queries=[
            EvaluationQuery(
                id="cal-answerable",
                split="calibration",
                query="which cases are relevant",
                relevant_case_ids=[CASE_A, CASE_B],
                expected_answer_points=["alpha", "beta"],
                tags=["answerable"],
                difficulty="medium",
            ),
            EvaluationQuery(
                id="test-abstention",
                split="test",
                query="question outside the corpus",
                relevant_case_ids=[],
                expected_answer_points=[],
                tags=["abstention"],
                difficulty="hard",
                should_abstain=True,
            ),
        ],
    )


def test_versioned_baseline_is_strict_and_reproducible() -> None:
    loaded = load_evaluation_set(BASELINE_PATH)

    assert loaded.evaluation_set.id == "alert-sage-rag-baseline"
    assert loaded.evaluation_set.version == "1.0.0"
    assert len(loaded.evaluation_set.documents) == 6
    assert len(loaded.evaluation_set.queries) == 20
    assert {query.split for query in loaded.evaluation_set.queries} == {
        "calibration",
        "test",
    }
    assert loaded.sha256 == "f9ad6fa91c0c231985884081d1427dffeb85f32cb352268c865fa3b3e9ae02a6"


@pytest.mark.parametrize(
    ("should_abstain", "relevant_case_ids", "answer_points"),
    [
        (False, [], ["answer"]),
        (False, [CASE_A], []),
        (True, [CASE_A], []),
        (True, [], ["answer"]),
    ],
)
def test_query_ground_truth_rejects_contradictory_labels(
    should_abstain: bool,
    relevant_case_ids: list[UUID],
    answer_points: list[str],
) -> None:
    with pytest.raises(ValidationError):
        EvaluationQuery(
            id="invalid-query",
            split="test",
            query="invalid ground truth",
            relevant_case_ids=relevant_case_ids,
            expected_answer_points=answer_points,
            tags=["invalid"],
            difficulty="easy",
            should_abstain=should_abstain,
        )


def test_evaluation_set_rejects_unknown_case_references_and_missing_split() -> None:
    with pytest.raises(ValidationError, match="unknown case ids"):
        RagEvaluationSet(
            id="invalid-evaluation-set",
            version="1.0.0",
            name="Invalid evaluation set",
            description="Contains an unknown case reference",
            documents=[document("case-alpha", CASE_A)],
            queries=[
                EvaluationQuery(
                    id="cal-unknown",
                    split="calibration",
                    query="unknown case",
                    relevant_case_ids=[CASE_B],
                    expected_answer_points=["unknown"],
                    tags=["invalid"],
                    difficulty="easy",
                ),
                EvaluationQuery(
                    id="test-abstention",
                    split="test",
                    query="outside corpus",
                    relevant_case_ids=[],
                    expected_answer_points=[],
                    tags=["abstention"],
                    difficulty="easy",
                    should_abstain=True,
                ),
            ],
        )

    valid = evaluation_set().model_dump(mode="json")
    valid["queries"][1]["split"] = "calibration"
    with pytest.raises(ValidationError, match="calibration and test"):
        RagEvaluationSet.model_validate(valid)


def test_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")

    with pytest.raises(EvaluationSetLoadError, match="duplicate JSON key"):
        load_evaluation_set(path)


def test_metrics_calculate_document_recall_rank_abstention_and_latency() -> None:
    report = evaluate_retrieval(
        evaluation_set(),
        [
            RetrievalObservation(
                query_id="cal-answerable",
                retrieved_case_ids=[CASE_C, CASE_A],
                latency_ms=10,
            ),
            RetrievalObservation(
                query_id="test-abstention",
                retrieved_case_ids=[CASE_C],
                latency_ms=40,
            ),
        ],
        top_k=3,
    )

    assert report.overall.query_count == 2
    assert report.overall.source_hit_rate_at_k == 1.0
    assert report.overall.recall_at_k == 0.5
    assert report.overall.mean_reciprocal_rank == 0.5
    assert report.overall.abstention_accuracy == 0.0
    assert report.overall.no_answer_false_positive_rate == 1.0
    assert report.overall.error_rate == 0.0
    assert report.overall.latency_p50_ms == 25.0
    assert report.overall.latency_p95_ms == 38.5
    assert report.by_split["calibration"].query_count == 1
    assert report.by_split["test"].query_count == 1


def test_metrics_deduplicate_chunks_before_applying_top_k() -> None:
    report = evaluate_retrieval(
        evaluation_set(),
        [
            RetrievalObservation(
                query_id="cal-answerable",
                retrieved_case_ids=[CASE_A, CASE_A, CASE_B],
                latency_ms=1,
            ),
            RetrievalObservation(
                query_id="test-abstention",
                retrieved_case_ids=[],
                latency_ms=1,
            ),
        ],
        top_k=2,
    )

    assert report.overall.recall_at_k == 1.0
    assert report.overall.mean_reciprocal_rank == 1.0
    assert report.overall.abstention_accuracy == 1.0


def test_retrieval_errors_are_not_counted_as_correct_abstention() -> None:
    report = evaluate_retrieval(
        evaluation_set(),
        [
            RetrievalObservation(
                query_id="cal-answerable",
                retrieved_case_ids=[],
                latency_ms=5,
                error_code="provider-timeout",
            ),
            RetrievalObservation(
                query_id="test-abstention",
                retrieved_case_ids=[],
                latency_ms=5,
                error_code="provider-timeout",
            ),
        ],
        top_k=3,
    )

    assert report.overall.error_rate == 1.0
    assert report.overall.recall_at_k == 0.0
    assert report.overall.abstention_accuracy == 0.0
    assert report.overall.no_answer_false_positive_rate is None


@pytest.mark.parametrize("mode", ["missing", "unknown", "duplicate"])
def test_metrics_require_exact_observation_coverage(mode: str) -> None:
    observations = [
        RetrievalObservation(
            query_id="cal-answerable",
            retrieved_case_ids=[],
            latency_ms=1,
        ),
        RetrievalObservation(
            query_id="test-abstention",
            retrieved_case_ids=[],
            latency_ms=1,
        ),
    ]
    if mode == "missing":
        observations.pop()
    elif mode == "unknown":
        observations[-1] = RetrievalObservation(
            query_id="unknown-query",
            retrieved_case_ids=[],
            latency_ms=1,
        )
    else:
        observations[-1] = observations[0]

    with pytest.raises(EvaluationObservationError):
        evaluate_retrieval(evaluation_set(), observations, top_k=3)
