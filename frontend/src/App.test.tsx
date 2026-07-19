import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AlertResponse,
  ApiErrorResponse,
  WorkflowDetailResponse,
} from "./api/generated";
import { renderApp } from "./test/renderApp";

const ALERT: AlertResponse = {
  id: "019f73e2-c928-70b2-b37a-10f876a72565",
  source: "web",
  external_alert_id: "alert-20260718-001",
  fingerprint: "a".repeat(64),
  alert_name: "HighCPUUsage",
  service: "order-service",
  instance: "order-service-01",
  severity: "critical",
  status: "received",
  payload: {
    summary: "CPU 使用率持续 5 分钟超过阈值",
    value: 92.5,
    threshold: 80,
  },
  started_at: "2026-07-18T06:30:00Z",
  created_at: "2026-07-18T06:31:00Z",
  updated_at: "2026-07-18T06:31:00Z",
};

const WORKFLOW: WorkflowDetailResponse = {
  run: {
    id: "119f73e2-c928-70b2-b37a-10f876a72565",
    alert_id: ALERT.id,
    thread_id: "workflow-thread-001",
    workflow_version: "1.0",
    status: "waiting_for_approval",
    current_node: "human_review",
    attempt: 1,
    error_code: null,
    error_message: null,
    started_at: "2026-07-18T06:31:10Z",
    finished_at: null,
    created_at: "2026-07-18T06:31:05Z",
    updated_at: "2026-07-18T06:31:20Z",
  },
  report: {
    id: "219f73e2-c928-70b2-b37a-10f876a72565",
    workflow_run_id: "119f73e2-c928-70b2-b37a-10f876a72565",
    version: 1,
    schema_version: "1.0",
    summary: "订单服务 CPU 升高与突发流量相关。",
    root_causes: [],
    evidence: [],
    recommendations: [{ title: "检查慢查询并临时扩容" }],
    confidence: "0.8600",
    model_name: "mock-diagnostic-model",
    prompt_version: "diagnosis-v1",
    created_at: "2026-07-18T06:31:20Z",
  },
  decision: null,
};

function jsonResponse(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function apiError(code: string, message: string, context: Record<string, unknown> = {}): ApiErrorResponse {
  return { error: { code, message, context } };
}

function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  if (init?.method) return init.method;
  return input instanceof Request ? input.method : "GET";
}

function healthResponse(): Response {
  return jsonResponse({ status: "ok", service: "Alert Sage API", version: "0.1.0" });
}

function workflowNotFoundResponse(): Response {
  return jsonResponse(apiError("workflow_not_found", "not found"), 404);
}

describe("Alert Sage routes", () => {
  beforeEach(() => {
    vi.stubGlobal("scrollTo", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads a filtered alert list from URL state", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.includes("/health/live")) return healthResponse();
      return jsonResponse({ items: [ALERT], total: 1, page: 1, page_size: 10, pages: 1 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp("/alerts?severity=critical&service=order-service");

    expect(await screen.findByRole("heading", { name: "告警中心" })).toBeInTheDocument();
    expect(await screen.findByText("alert-20260718-001")).toBeInTheDocument();
    expect(screen.getByText("已接收")).toBeInTheDocument();
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([input]) => requestUrl(input));
      expect(urls.some((url) => url.includes("severity=critical"))).toBe(true);
      expect(urls.some((url) => url.includes("service=order-service"))).toBe(true);
    });
  });

  it("renders list empty and error states", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(async () => healthResponse())
      .mockImplementationOnce(async () =>
        jsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const view = renderApp("/alerts");

    expect(await screen.findByText("暂无符合条件的告警")).toBeInTheDocument();
    view.unmount();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        requestUrl(input).includes("/health/live")
          ? healthResponse()
          : jsonResponse(apiError("service_unavailable", "unavailable"), 503),
      ),
    );
    renderApp("/alerts");

    expect(await screen.findByText("告警列表加载失败")).toBeInTheDocument();
  });

  it("creates an alert and navigates to its detail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url.includes("/health/live")) return healthResponse();
      if (url.endsWith("/workflow") && requestMethod(input, init) === "GET") {
        return workflowNotFoundResponse();
      }
      if (requestMethod(input, init) === "POST") {
        return jsonResponse(ALERT, 201, { "X-Idempotent-Replay": "false" });
      }
      return jsonResponse(ALERT);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp("/alerts/new");

    fireEvent.click(
      await screen.findByRole("button", { name: "创建并查看详情" }, { timeout: 10_000 }),
    );

    expect(
      await screen.findByRole("heading", { level: 1, name: "HighCPUUsage" }, { timeout: 5_000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("CPU 使用率持续 5 分钟超过阈值", { exact: false })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true);
  }, 20_000);

  it("offers a link to the existing alert on idempotency conflict", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url.includes("/health/live")) return healthResponse();
      if (url.endsWith("/workflow") && requestMethod(input, init) === "GET") {
        return workflowNotFoundResponse();
      }
      if (requestMethod(input, init) === "POST") {
        return jsonResponse(
          apiError("alert_idempotency_conflict", "conflict", {
            alert_id: ALERT.id,
            href: `/api/v1/alerts/${ALERT.id}`,
          }),
          409,
        );
      }
      return jsonResponse(ALERT);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp("/alerts/new");

    fireEvent.click(
      await screen.findByRole("button", { name: "创建并查看详情" }, { timeout: 10_000 }),
    );
    expect(
      await screen.findByText("外部告警 ID 已被不同内容使用", {}, { timeout: 5_000 }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看已有告警" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "HighCPUUsage" }, { timeout: 5_000 }),
    ).toBeInTheDocument();
  }, 20_000);

  it("renders a diagnosis report and submits a human decision", async () => {
    class MockEventSource {
      onmessage: ((event: MessageEvent) => void) | null = null;

      addEventListener(): void {}

      removeEventListener(): void {}

      close(): void {}
    }

    vi.stubGlobal("EventSource", MockEventSource);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url.includes("/health/live")) return healthResponse();
      if (url.endsWith("/events?after=0")) {
        return jsonResponse({ items: [], last_sequence: 0 });
      }
      if (url.endsWith("/workflow")) return jsonResponse(WORKFLOW);
      if (url.endsWith("/decisions") && requestMethod(input, init) === "POST") {
        return jsonResponse(
          {
            workflow_run_id: WORKFLOW.run.id,
            status: "waiting_for_approval",
            dispatched: true,
          },
          202,
        );
      }
      return jsonResponse(ALERT);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp(`/alerts/${ALERT.id}`);

    expect(await screen.findByText("订单服务 CPU 升高与突发流量相关。"))
      .toBeInTheDocument();
    expect(screen.getByText("86%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /批准建议/ }));

    await waitFor(() => {
      const decisionCall = fetchMock.mock.calls.find(([input]) =>
        requestUrl(input).endsWith("/decisions"),
      );
      expect(decisionCall?.[1]?.method).toBe("POST");
      expect(JSON.parse(String(decisionCall?.[1]?.body))).toMatchObject({
        action: "approve",
        actor: "demo-user",
      });
    });
  });

  it("renders alert and route not-found states", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        requestUrl(input).includes("/health/live")
          ? healthResponse()
          : jsonResponse(apiError("alert_not_found", "not found"), 404),
      ),
    );
    const view = renderApp("/alerts/00000000-0000-0000-0000-000000000000");

    expect(await screen.findByText("告警不存在", {}, { timeout: 5_000 })).toBeInTheDocument();
    view.unmount();

    renderApp("/missing-page");
    expect(await screen.findByText("页面不存在", {}, { timeout: 5_000 })).toBeInTheDocument();
  });
});
