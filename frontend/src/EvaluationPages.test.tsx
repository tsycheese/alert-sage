import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiErrorResponse,
  RagEvaluationDetailResponse,
  RagEvaluationResultResponse,
  RagEvaluationRunResponse,
} from "./api/generated";
import { renderApp } from "./test/renderApp";

const LEFT_RUN_ID = "5ebf1360-0d53-4244-ae82-250c04cee4e4";
const RIGHT_RUN_ID = "75f4fff5-9e4f-46df-8e86-1d679e318da2";

function metrics({ abstention, falsePositive }: { abstention: number; falsePositive: number }) {
  return {
    top_k: 3,
    overall: {
      query_count: 12,
      answerable_count: 10,
      abstention_count: 2,
      error_count: 0,
      source_hit_rate_at_k: 1,
      recall_at_k: 1,
      mean_reciprocal_rank: 1,
      abstention_accuracy: abstention,
      no_answer_false_positive_rate: falsePositive,
      error_rate: 0,
      latency_p50_ms: 673,
      latency_p95_ms: 2028,
    },
    by_split: {},
  };
}

const LEFT_RUN: RagEvaluationRunResponse = {
  id: LEFT_RUN_ID,
  idempotency_key: "rag-calibration-left",
  evaluation_set_id: "alert-sage-rag-baseline",
  evaluation_set_version: "1.0.0",
  evaluation_set_sha256: "f9ad6fa91c0c231985884081d1427dffeb85f32cb352268c865fa3b3e9ae02a6",
  build_revision: null,
  provider: "dify",
  dataset_id: "70000000-0000-4000-8000-000000000007",
  split: "calibration",
  top_k: 3,
  score_threshold: null,
  status: "completed",
  attempt: 1,
  query_count: 12,
  completed_query_count: 12,
  summary_metrics: metrics({ abstention: 0, falsePositive: 1 }),
  error_code: null,
  error_message: null,
  started_at: "2026-07-26T07:22:39Z",
  finished_at: "2026-07-26T07:23:55Z",
  created_at: "2026-07-26T07:22:35Z",
  updated_at: "2026-07-26T07:23:55Z",
};

const RIGHT_RUN: RagEvaluationRunResponse = {
  ...LEFT_RUN,
  id: RIGHT_RUN_ID,
  idempotency_key: "rag-calibration-right",
  score_threshold: 0.4,
  summary_metrics: metrics({ abstention: 1, falsePositive: 0 }),
  created_at: "2026-07-26T07:25:35Z",
  started_at: "2026-07-26T07:25:39Z",
  finished_at: "2026-07-26T07:26:55Z",
  updated_at: "2026-07-26T07:26:55Z",
};

const ANSWER_RESULT: RagEvaluationResultResponse = {
  id: "11111111-0000-4000-8000-000000000001",
  run_id: RIGHT_RUN_ID,
  query_id: "cal-checkout-cpu-direct",
  split: "calibration",
  difficulty: "easy",
  query_text: "checkout 发布后 CPU 超过 90%，SQL 查询变慢",
  ground_truth: {
    relevant_case_ids: ["11111111-1111-4111-8111-111111111111"],
    should_abstain: false,
  },
  retrieved_items: [
    {
      rank: 1,
      case_id: "11111111-1111-4111-8111-111111111111",
      document_name: "alert-sage-case-11111111-1111-4111-8111-111111111111.md",
      score: 0.6751,
      source: "dify://evaluation/document/chunk",
    },
  ],
  source_hit_at_k: true,
  recall_at_k: 1,
  reciprocal_rank: 1,
  abstention_correct: null,
  false_positive: null,
  latency_ms: 680,
  error_code: null,
  created_at: "2026-07-26T07:26:55Z",
};

const ABSTENTION_RESULT: RagEvaluationResultResponse = {
  ...ANSWER_RESULT,
  id: "22222222-0000-4000-8000-000000000002",
  query_id: "cal-abstain-domain-transfer",
  query_text: "How do I transfer the company domain?",
  difficulty: "medium",
  ground_truth: { relevant_case_ids: [], should_abstain: true },
  retrieved_items: [],
  source_hit_at_k: null,
  recall_at_k: null,
  reciprocal_rank: null,
  abstention_correct: true,
  false_positive: false,
  latency_ms: 590,
};

const LEFT_DETAIL: RagEvaluationDetailResponse = {
  run: LEFT_RUN,
  results: [ANSWER_RESULT, { ...ABSTENTION_RESULT, run_id: LEFT_RUN_ID, abstention_correct: false, false_positive: true }],
};
const RIGHT_DETAIL: RagEvaluationDetailResponse = {
  run: RIGHT_RUN,
  results: [ANSWER_RESULT, ABSTENTION_RESULT],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function apiError(code: string, message: string): ApiErrorResponse {
  return { error: { code, message, context: {} } };
}

function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
}

function healthResponse(): Response {
  return jsonResponse({ status: "ok", service: "Alert Sage API", version: "0.1.0" });
}

function evaluationFetch(input: RequestInfo | URL): Response {
  const url = requestUrl(input);
  if (url.includes("/health/live")) return healthResponse();
  if (url.includes(`/rag/evaluations/runs/${LEFT_RUN_ID}`)) return jsonResponse(LEFT_DETAIL);
  if (url.includes(`/rag/evaluations/runs/${RIGHT_RUN_ID}`)) return jsonResponse(RIGHT_DETAIL);
  if (url.includes("/rag/evaluations/runs")) {
    return jsonResponse({ items: [RIGHT_RUN, LEFT_RUN], total: 2, page: 1, page_size: 10, pages: 1 });
  }
  return jsonResponse(apiError("not_found", "not found"), 404);
}

describe("RAG evaluation pages", () => {
  beforeEach(() => {
    vi.stubGlobal("scrollTo", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads filtered evaluation runs and exposes traceable summaries", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => evaluationFetch(input));
    vi.stubGlobal("fetch", fetchMock);
    renderApp("/evaluations?split=calibration&status=completed");

    expect(await screen.findByRole("heading", { name: "RAG 评测" })).toBeInTheDocument();
    expect(await screen.findByText(RIGHT_RUN_ID)).toBeInTheDocument();
    expect(screen.getAllByText("已完成").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("button", { name: /对比已选运行/ })).toBeDisabled();
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([input]) => requestUrl(input));
      expect(
        urls.some((url) => url.includes("split=calibration") && url.includes("status=completed")),
      ).toBe(true);
    });
  });

  it("renders metric cards and per-query retrieval diagnostics", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => evaluationFetch(input)));
    renderApp(`/evaluations/${RIGHT_RUN_ID}`);

    expect(await screen.findByRole("heading", { name: "评测运行详情" })).toBeInTheDocument();
    expect(screen.getByText("Top3 命中率")).toBeInTheDocument();
    expect(screen.getAllByText("100.0%").length).toBeGreaterThan(1);
    expect(screen.getByText("正确拒答")).toBeInTheDocument();
    expect(screen.getByText("cal-checkout-cpu-direct")).toBeInTheDocument();
    expect(screen.getByText("67.5%")).toBeInTheDocument();
  });

  it("compares compatible runs and shows metric improvement", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => evaluationFetch(input)));
    renderApp(`/evaluations/compare?left=${LEFT_RUN_ID}&right=${RIGHT_RUN_ID}`);

    expect(await screen.findByRole("heading", { name: "评测运行对比" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "核心指标变化" })).toBeInTheDocument();
    expect(screen.getByText("+100.0 pp")).toBeInTheDocument();
    expect(screen.getByText("-100.0 pp")).toBeInTheDocument();
    expect(screen.getAllByText("正确拒答").length).toBeGreaterThan(0);
  });

  it("blocks comparisons across different splits", async () => {
    const incompatible = {
      ...RIGHT_DETAIL,
      run: { ...RIGHT_RUN, split: "test" as const },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = requestUrl(input);
        if (url.includes("/health/live")) return healthResponse();
        if (url.includes(LEFT_RUN_ID)) return jsonResponse(LEFT_DETAIL);
        if (url.includes(RIGHT_RUN_ID)) return jsonResponse(incompatible);
        return jsonResponse(apiError("not_found", "not found"), 404);
      }),
    );
    renderApp(`/evaluations/compare?left=${LEFT_RUN_ID}&right=${RIGHT_RUN_ID}`);

    expect(await screen.findByText("两条运行不可比较")).toBeInTheDocument();
    expect(screen.getByText(/相同的评测集 ID、版本、SHA 和 split/)).toBeInTheDocument();
  });

  it("renders evaluation list empty and error states", async () => {
    const firstFetch = vi.fn(async (input: RequestInfo | URL) =>
      requestUrl(input).includes("/health/live")
        ? healthResponse()
        : jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 }),
    );
    vi.stubGlobal("fetch", firstFetch);
    const view = renderApp("/evaluations");
    expect(await screen.findByText("暂无符合条件的评测运行")).toBeInTheDocument();
    view.unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        requestUrl(input).includes("/health/live")
          ? healthResponse()
          : jsonResponse(apiError("service_unavailable", "unavailable"), 503),
      ),
    );
    renderApp("/evaluations");
    expect(await screen.findByText("评测运行加载失败")).toBeInTheDocument();
  });
});
