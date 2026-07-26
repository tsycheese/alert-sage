import type {
  RagEvaluationResultResponse,
  RagEvaluationRunResponse,
  RagEvaluationRunStatus,
  RagEvaluationSplit,
} from "../../api/generated";

export interface EvaluationAggregateMetrics {
  source_hit_rate_at_k: number | null;
  recall_at_k: number | null;
  mean_reciprocal_rank: number | null;
  abstention_accuracy: number | null;
  no_answer_false_positive_rate: number | null;
  error_rate: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
}

export interface EvaluationRetrievedItem {
  rank: number;
  caseId: string | null;
  documentName: string;
  score: number;
  source: string;
}

export const evaluationStatusLabels: Record<RagEvaluationRunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};

export const evaluationStatusColors: Record<RagEvaluationRunStatus, string> = {
  queued: "default",
  running: "processing",
  completed: "success",
  failed: "error",
};

export const evaluationSplitLabels: Record<RagEvaluationSplit, string> = {
  calibration: "Calibration",
  test: "Test",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function readEvaluationMetrics(
  run: RagEvaluationRunResponse,
): EvaluationAggregateMetrics | null {
  if (!isRecord(run.summary_metrics) || !isRecord(run.summary_metrics.overall)) return null;
  const overall = run.summary_metrics.overall;
  const errorRate = nullableNumber(overall.error_rate);
  if (errorRate === null) return null;
  return {
    source_hit_rate_at_k: nullableNumber(overall.source_hit_rate_at_k),
    recall_at_k: nullableNumber(overall.recall_at_k),
    mean_reciprocal_rank: nullableNumber(overall.mean_reciprocal_rank),
    abstention_accuracy: nullableNumber(overall.abstention_accuracy),
    no_answer_false_positive_rate: nullableNumber(overall.no_answer_false_positive_rate),
    error_rate: errorRate,
    latency_p50_ms: nullableNumber(overall.latency_p50_ms),
    latency_p95_ms: nullableNumber(overall.latency_p95_ms),
  };
}

export function readRetrievedItems(
  result: RagEvaluationResultResponse,
): EvaluationRetrievedItem[] {
  return result.retrieved_items.flatMap((value) => {
    if (!isRecord(value)) return [];
    const rank = nullableNumber(value.rank);
    const score = nullableNumber(value.score);
    if (rank === null || score === null || typeof value.document_name !== "string") return [];
    return [
      {
        rank,
        caseId: typeof value.case_id === "string" ? value.case_id : null,
        documentName: value.document_name,
        score,
        source: typeof value.source === "string" ? value.source : "",
      },
    ];
  });
}

export function isAbstentionQuery(result: RagEvaluationResultResponse): boolean {
  return result.ground_truth.should_abstain === true;
}

export function areRunsComparable(
  left: RagEvaluationRunResponse,
  right: RagEvaluationRunResponse,
): boolean {
  return (
    left.status === "completed" &&
    right.status === "completed" &&
    left.evaluation_set_id === right.evaluation_set_id &&
    left.evaluation_set_version === right.evaluation_set_version &&
    left.evaluation_set_sha256 === right.evaluation_set_sha256 &&
    left.split === right.split
  );
}

export function isEvaluationRunActive(run: RagEvaluationRunResponse): boolean {
  return run.status === "queued" || run.status === "running";
}

export function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function formatMilliseconds(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)} ms`;
}

export function formatThreshold(value: number | null): string {
  return value === null ? "未启用" : value.toFixed(2);
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "medium",
    hour12: false,
  }).format(new Date(value));
}
