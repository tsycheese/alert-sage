import { queryOptions } from "@tanstack/react-query";

import { getRagEvaluationRun, listRagEvaluationRuns } from "../../api/http";
import type { RagEvaluationListParams } from "../../api/http";
import { isEvaluationRunActive } from "./model";

export const evaluationKeys = {
  all: ["rag-evaluations"] as const,
  lists: () => [...evaluationKeys.all, "list"] as const,
  list: (params: RagEvaluationListParams) => [...evaluationKeys.lists(), params] as const,
  details: () => [...evaluationKeys.all, "detail"] as const,
  detail: (runId: string) => [...evaluationKeys.details(), runId] as const,
};

export function evaluationListOptions(params: RagEvaluationListParams) {
  return queryOptions({
    queryKey: evaluationKeys.list(params),
    queryFn: ({ signal }) => listRagEvaluationRuns(params, signal),
    refetchInterval: (query) =>
      query.state.data?.items.some(isEvaluationRunActive) ? 2_000 : false,
  });
}

export function evaluationDetailOptions(runId: string) {
  return queryOptions({
    queryKey: evaluationKeys.detail(runId),
    queryFn: ({ signal }) => getRagEvaluationRun(runId, signal),
    refetchInterval: (query) =>
      query.state.data && isEvaluationRunActive(query.state.data.run) ? 2_000 : false,
  });
}
