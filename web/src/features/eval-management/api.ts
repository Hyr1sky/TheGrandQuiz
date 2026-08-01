import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type EvalCandidate = components["schemas"]["EvalInboxCandidateV1"];
export type GradingSample = components["schemas"]["GradingCalibrationSample"];
export type DatasetSnapshot = components["schemas"]["DatasetSnapshotV1"];

export async function listEvalCandidates(): Promise<EvalCandidate[]> {
  const { data, error } = await apiClient.GET("/api/v1/eval/candidates");
  if (error !== undefined) throw toApiRequestError(error);
  return data.items;
}

export async function syncEvalCandidates(): Promise<EvalCandidate[]> {
  const { data, error } = await apiClient.POST("/api/v1/eval/candidates/sync");
  if (error !== undefined) throw toApiRequestError(error);
  return data.items;
}

export async function listDatasetSnapshots(): Promise<DatasetSnapshot[]> {
  const { data, error } = await apiClient.GET("/api/v1/eval/snapshots", {
    params: { query: { limit: 12 } },
  });
  if (error !== undefined) throw toApiRequestError(error);
  return data.items;
}

export async function importBlindLabels(
  samples: GradingSample[],
  requestId: string,
): Promise<EvalCandidate[]> {
  const { data, error } = await apiClient.POST(
    "/api/v1/eval/candidates/blind-import",
    { body: { request_id: requestId, samples } },
  );
  if (error !== undefined) throw toApiRequestError(error);
  return data.items;
}

export async function reviewEvalCandidate(
  candidateId: string,
  decision: "approved" | "rejected",
  requestId: string,
): Promise<EvalCandidate> {
  const { data, error } = await apiClient.POST(
    "/api/v1/eval/candidates/{candidate_id}/review",
    {
      params: { path: { candidate_id: candidateId } },
      body: {
        request_id: requestId,
        decision,
        reason: decision === "approved" ? "隐私检查通过" : "不进入数据集",
      },
    },
  );
  if (error !== undefined) throw toApiRequestError(error);
  return data;
}

export async function createDatasetSnapshot(
  candidateIds: string[],
): Promise<DatasetSnapshot> {
  const { data, error } = await apiClient.POST("/api/v1/eval/snapshots", {
    body: { candidate_ids: candidateIds },
  });
  if (error !== undefined) throw toApiRequestError(error);
  return data;
}
