import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type SafeTraceRun = components["schemas"]["SafeTraceRunV1"];
export type SafeTraceSummary = components["schemas"]["SafeTraceSummaryV1"];
export type SafeTraceEvent = components["schemas"]["SafeTraceEventV1"];

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
