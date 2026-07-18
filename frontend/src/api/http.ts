import type { HealthResponse } from "../types/health";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/health/live`, { signal });

  if (!response.ok) {
    throw new Error(`Health request failed with status ${response.status}`);
  }

  return response.json() as Promise<HealthResponse>;
}
