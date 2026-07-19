import type { AlertSeverity, AlertStatus } from "../../api/generated";
import type { AlertListParams } from "../../api/http";
import { ALERT_SEVERITIES, ALERT_STATUSES } from "./constants";

export const DEFAULT_PAGE_SIZE = 10;

function positiveInteger(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function isSeverity(value: string | null): value is AlertSeverity {
  return value !== null && ALERT_SEVERITIES.some((severity) => severity === value);
}

function isStatus(value: string | null): value is AlertStatus {
  return value !== null && ALERT_STATUSES.some((status) => status === value);
}

export function readAlertSearchParams(search: URLSearchParams): AlertListParams {
  const status = search.get("status");
  const severity = search.get("severity");
  const service = search.get("service")?.trim();
  return {
    ...(isStatus(status) ? { status } : {}),
    ...(isSeverity(severity) ? { severity } : {}),
    ...(service ? { service } : {}),
    page: positiveInteger(search.get("page"), 1),
    page_size: Math.min(100, positiveInteger(search.get("page_size"), DEFAULT_PAGE_SIZE)),
  };
}

export function writeAlertSearchParams(params: AlertListParams): URLSearchParams {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.severity) search.set("severity", params.severity);
  if (params.service) search.set("service", params.service);
  if (params.page !== 1) search.set("page", String(params.page));
  if (params.page_size !== DEFAULT_PAGE_SIZE) search.set("page_size", String(params.page_size));
  return search;
}
