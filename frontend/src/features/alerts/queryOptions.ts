import { queryOptions } from "@tanstack/react-query";

import { getAlert, getWorkflow, listAlerts, listWorkflowEvents } from "../../api/http";
import type { AlertListParams } from "../../api/http";

export const alertKeys = {
  all: ["alerts"] as const,
  lists: () => [...alertKeys.all, "list"] as const,
  list: (params: AlertListParams) => [...alertKeys.lists(), params] as const,
  details: () => [...alertKeys.all, "detail"] as const,
  detail: (alertId: string) => [...alertKeys.details(), alertId] as const,
  workflow: (alertId: string) => [...alertKeys.detail(alertId), "workflow"] as const,
  workflowEvents: (alertId: string) => [...alertKeys.workflow(alertId), "events"] as const,
};

export function alertListOptions(params: AlertListParams) {
  return queryOptions({
    queryKey: alertKeys.list(params),
    queryFn: ({ signal }) => listAlerts(params, signal),
  });
}

export function alertWorkflowOptions(alertId: string) {
  return queryOptions({
    queryKey: alertKeys.workflow(alertId),
    queryFn: ({ signal }) => getWorkflow(alertId, signal),
    retry: (failureCount, error) =>
      error instanceof Error && "status" in error && error.status === 404
        ? false
        : failureCount < 2,
  });
}

export function alertWorkflowEventsOptions(alertId: string) {
  return queryOptions({
    queryKey: alertKeys.workflowEvents(alertId),
    queryFn: ({ signal }) => listWorkflowEvents(alertId, 0, signal),
  });
}

export function alertDetailOptions(alertId: string) {
  return queryOptions({
    queryKey: alertKeys.detail(alertId),
    queryFn: ({ signal }) => getAlert(alertId, signal),
  });
}
