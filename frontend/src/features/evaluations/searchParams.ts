import type { RagEvaluationRunStatus, RagEvaluationSplit } from "../../api/generated";
import type { RagEvaluationListParams } from "../../api/http";

export const DEFAULT_EVALUATION_PAGE_SIZE = 10;
const VALID_SPLITS = new Set<RagEvaluationSplit>(["calibration", "test"]);
const VALID_STATUSES = new Set<RagEvaluationRunStatus>([
  "queued",
  "running",
  "completed",
  "failed",
]);

function positiveInteger(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function readEvaluationSearchParams(search: URLSearchParams): RagEvaluationListParams {
  const split = search.get("split") as RagEvaluationSplit | null;
  const status = search.get("status") as RagEvaluationRunStatus | null;
  return {
    split: split && VALID_SPLITS.has(split) ? split : undefined,
    status: status && VALID_STATUSES.has(status) ? status : undefined,
    page: positiveInteger(search.get("page"), 1),
    page_size: Math.min(100, positiveInteger(search.get("page_size"), DEFAULT_EVALUATION_PAGE_SIZE)),
  };
}

export function writeEvaluationSearchParams(params: RagEvaluationListParams): URLSearchParams {
  const search = new URLSearchParams();
  if (params.split) search.set("split", params.split);
  if (params.status) search.set("status", params.status);
  if (params.page !== 1) search.set("page", String(params.page));
  if (params.page_size !== DEFAULT_EVALUATION_PAGE_SIZE) {
    search.set("page_size", String(params.page_size));
  }
  return search;
}
