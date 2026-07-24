import type { components } from "../../shared/api/generated/schema";
import { apiClient, toApiRequestError } from "../../shared/api/client";

export type ResourceSummary = components["schemas"]["ResourceSummary"];
export type DocumentNodeSummary = components["schemas"]["DocumentNodeSummary"];
export type DocumentNodeRead = components["schemas"]["DocumentNodeReadResponse"];
export type RunView = components["schemas"]["RunView"];
export type GroundedAnswerResult = components["schemas"]["GroundedAnswerResult"];
export type ResolvedCitation = components["schemas"]["ResolvedCitation"];
export type UiEvent = components["schemas"]["UiEvent"];

export async function listResources(): Promise<ResourceSummary[]> {
  const { data, error } = await apiClient.GET("/api/v1/resources");
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data.items;
}

export async function getOutline(resourceId: string): Promise<DocumentNodeSummary[]> {
  const { data, error } = await apiClient.GET("/api/v1/resources/{resource_id}/outline", {
    params: { path: { resource_id: resourceId } },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data.nodes;
}

export async function readNode(
  resourceId: string,
  nodeId: string,
): Promise<DocumentNodeRead> {
  const { data, error } = await apiClient.GET(
    "/api/v1/resources/{resource_id}/nodes/{node_id}",
    {
      params: {
        path: { resource_id: resourceId, node_id: nodeId },
        query: { max_chars: 4000 },
      },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function startQuestion(resourceId: string, query: string): Promise<RunView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/resources/{resource_id}/questions",
    {
      params: { path: { resource_id: resourceId } },
      body: { query },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function getRun(runId: string): Promise<RunView> {
  const { data, error } = await apiClient.GET("/api/v1/runs/{run_id}", {
    params: { path: { run_id: runId } },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function cancelRun(runId: string): Promise<RunView> {
  const { data, error } = await apiClient.POST("/api/v1/runs/{run_id}/cancel", {
    params: { path: { run_id: runId } },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}
