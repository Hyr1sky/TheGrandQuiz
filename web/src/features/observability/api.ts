import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type TraceSnapshot = components["schemas"]["TraceSnapshot"];
export type TraceSummary = components["schemas"]["TraceSummary"];
export type TraceSpanView = components["schemas"]["TraceSpanView"];
export type TraceUiEvent = components["schemas"]["TraceUiEvent"];

export async function getTraceSnapshot(
  traceId: string,
): Promise<TraceSnapshot> {
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
