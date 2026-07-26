import { apiClient, toApiRequestError } from "./client";
import type { components } from "./generated/schema";

export type ResourceSummary = components["schemas"]["ResourceSummary"];

export async function listResources(): Promise<ResourceSummary[]> {
  const { data, error } = await apiClient.GET("/api/v1/resources");
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data.items;
}
