import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type SafeTraceRun = components["schemas"]["SafeTraceRunV1"];
export type SafeTraceSummary = components["schemas"]["SafeTraceSummaryV1"];
export type SafeTraceEvent = components["schemas"]["SafeTraceEventV1"];
export type SafeTraceStatus = SafeTraceRun["status"];

export async function listTraceSnapshots(
  status: SafeTraceStatus | null,
  limit = 20,
): Promise<SafeTraceRun[]> {
  const { data, error } = await apiClient.GET(
    "/api/v1/observability/traces",
    {
      params: {
        query: {
          status,
          limit,
        },
      },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function getTraceSnapshot(
  traceId: string,
): Promise<SafeTraceRun> {
  const { data, error } = await apiClient.GET(
    "/api/v1/observability/traces/{trace_id}",
    {
      params: { path: { trace_id: traceId } },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}
