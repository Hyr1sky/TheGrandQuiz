import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type AcquisitionView = components["schemas"]["AcquisitionView"];
export type AcquisitionCreated = components["schemas"]["AcquisitionCreated"];
export type AcquisitionUiEvent = components["schemas"]["AcquisitionUiEvent"];

export async function createUpload(
  filename: string,
  content: string,
): Promise<AcquisitionCreated> {
  const { data, error } = await apiClient.POST("/api/v1/acquisitions", {
    body: { kind: "upload", filename, content },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function createUrl(
  url: string,
): Promise<AcquisitionCreated> {
  const { data, error } = await apiClient.POST("/api/v1/acquisitions", {
    body: { kind: "url", url },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function getAcquisition(
  runId: string,
): Promise<AcquisitionView> {
  const { data, error } = await apiClient.GET(
    "/api/v1/acquisitions/{run_id}",
    { params: { path: { run_id: runId } } },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function listAcquisitions(): Promise<AcquisitionView[]> {
  const { data, error } = await apiClient.GET("/api/v1/acquisitions", {
    params: { query: { limit: 12 } },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data.items;
}

export async function approveAcquisition(
  runId: string,
  resumeToken: string,
  approvedItemIds: string[],
): Promise<AcquisitionView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/acquisitions/{run_id}/approval",
    {
      params: { path: { run_id: runId } },
      body: {
        resume_token: resumeToken,
        approved_item_ids: approvedItemIds,
      },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function cancelAcquisition(
  runId: string,
  resumeToken: string,
): Promise<AcquisitionView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/acquisitions/{run_id}/cancel",
    {
      params: { path: { run_id: runId } },
      body: { resume_token: resumeToken },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}
