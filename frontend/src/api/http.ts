import type {
  AlertCreate,
  AlertListResponse,
  AlertResponse,
  AlertSeverity,
  AlertStatus,
  ApiErrorResponse,
  HealthResponse,
  HumanDecisionRequest,
  WorkflowAcceptedResponse,
  WorkflowDetailResponse,
  WorkflowEventListResponse,
  WorkflowStartCommand,
} from "./generated";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export interface AlertListParams {
  status?: AlertStatus;
  severity?: AlertSeverity;
  service?: string;
  page: number;
  page_size: number;
}

export interface CreateAlertResult {
  alert: AlertResponse;
  replayed: boolean;
}

export class ApiClientError extends Error {
  readonly status: number;
  readonly body: ApiErrorResponse | null;

  constructor(status: number, body: ApiErrorResponse | null) {
    super(body?.error.message ?? `API request failed with status ${status}`);
    this.name = "ApiClientError";
    this.status = status;
    this.body = body;
  }
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string"
  );
}

async function parseJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return null;
  }
  return response.json() as Promise<unknown>;
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<{ data: T; response: Response }> {
  const response = await fetch(`${apiBaseUrl}${path}`, init);
  const body = await parseJson(response);

  if (!response.ok) {
    throw new ApiClientError(response.status, isApiErrorResponse(body) ? body : null);
  }

  return { data: body as T, response };
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const { data } = await requestJson<HealthResponse>("/api/v1/health/live", { signal });
  return data;
}

export async function listAlerts(
  params: AlertListParams,
  signal?: AbortSignal,
): Promise<AlertListResponse> {
  const search = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.page_size),
  });
  if (params.status) search.set("status", params.status);
  if (params.severity) search.set("severity", params.severity);
  if (params.service) search.set("service", params.service);

  const { data } = await requestJson<AlertListResponse>(
    `/api/v1/alerts?${search.toString()}`,
    { signal },
  );
  return data;
}

export async function getAlert(alertId: string, signal?: AbortSignal): Promise<AlertResponse> {
  const { data } = await requestJson<AlertResponse>(
    `/api/v1/alerts/${encodeURIComponent(alertId)}`,
    { signal },
  );
  return data;
}

export async function createAlert(command: AlertCreate): Promise<CreateAlertResult> {
  const { data, response } = await requestJson<AlertResponse>("/api/v1/alerts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  return {
    alert: data,
    replayed: response.headers.get("x-idempotent-replay") === "true",
  };
}

export async function getWorkflow(
  alertId: string,
  signal?: AbortSignal,
): Promise<WorkflowDetailResponse> {
  const { data } = await requestJson<WorkflowDetailResponse>(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/workflow`,
    { signal },
  );
  return data;
}

export async function listWorkflowEvents(
  alertId: string,
  after = 0,
  signal?: AbortSignal,
): Promise<WorkflowEventListResponse> {
  const { data } = await requestJson<WorkflowEventListResponse>(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/events?after=${after}`,
    { signal },
  );
  return data;
}

export async function startWorkflow(
  alertId: string,
  command: WorkflowStartCommand,
): Promise<WorkflowAcceptedResponse> {
  const { data } = await requestJson<WorkflowAcceptedResponse>(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/workflow`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    },
  );
  return data;
}

export async function submitWorkflowDecision(
  alertId: string,
  command: HumanDecisionRequest,
): Promise<WorkflowAcceptedResponse> {
  const { data } = await requestJson<WorkflowAcceptedResponse>(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/decisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    },
  );
  return data;
}

export async function retryWorkflow(alertId: string): Promise<WorkflowAcceptedResponse> {
  const { data } = await requestJson<WorkflowAcceptedResponse>(
    `/api/v1/alerts/${encodeURIComponent(alertId)}/retry`,
    { method: "POST" },
  );
  return data;
}

export function workflowStreamUrl(alertId: string): string {
  return `${apiBaseUrl}/api/v1/alerts/${encodeURIComponent(alertId)}/stream`;
}
