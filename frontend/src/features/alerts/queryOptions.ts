import { queryOptions } from "@tanstack/react-query";

import { getAlert, listAlerts } from "../../api/http";
import type { AlertListParams } from "../../api/http";

export const alertKeys = {
  all: ["alerts"] as const,
  lists: () => [...alertKeys.all, "list"] as const,
  list: (params: AlertListParams) => [...alertKeys.lists(), params] as const,
  details: () => [...alertKeys.all, "detail"] as const,
  detail: (alertId: string) => [...alertKeys.details(), alertId] as const,
};

export function alertListOptions(params: AlertListParams) {
  return queryOptions({
    queryKey: alertKeys.list(params),
    queryFn: ({ signal }) => listAlerts(params, signal),
  });
}

export function alertDetailOptions(alertId: string) {
  return queryOptions({
    queryKey: alertKeys.detail(alertId),
    queryFn: ({ signal }) => getAlert(alertId, signal),
  });
}
