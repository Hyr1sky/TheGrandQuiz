import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type AssessmentView = components["schemas"]["AssessmentView"];
export type KnowledgeFacetInventory =
  components["schemas"]["KnowledgeFacetInventoryV1"];
export type KnowledgeKind = NonNullable<
  components["schemas"]["AssessmentStartRequest"]["knowledge_kinds"]
>[number];
export type VerdictLabel =
  components["schemas"]["VerdictCorrectionRequest"]["final_verdict"];
export type VoiceRunView = components["schemas"]["VoiceRunView"];
export type VoiceRuntimeConfig = components["schemas"]["VoiceRuntimeConfig"];

export type AssessmentStartPlan =
  | {
      rounds: number;
      questionType: string | null;
      knowledgeKinds?: KnowledgeKind[];
    }
  | {
      questionTypePlan: Array<string | null>;
    };

export async function startAssessment(
  resourceId: string,
  plan: AssessmentStartPlan,
): Promise<AssessmentView> {
  const planBody =
    "questionTypePlan" in plan
      ? {
          rounds: plan.questionTypePlan.length,
          question_type_plan: plan.questionTypePlan,
        }
      : {
          rounds: plan.rounds,
          question_type: plan.questionType,
          ...(plan.knowledgeKinds && plan.knowledgeKinds.length > 0
            ? { knowledge_kinds: plan.knowledgeKinds }
            : {}),
        };
  const { data, error } = await apiClient.POST("/api/v1/assessments", {
    body: {
      resource_ids: [resourceId],
      ...planBody,
      focus: "mixed",
    },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function getKnowledgeFacets(
  resourceId: string,
): Promise<KnowledgeFacetInventory> {
  const { data, error } = await apiClient.GET("/api/v1/learning/facets", {
    params: { query: { resource_id: resourceId } },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function correctVerdict(
  attemptId: string,
  finalVerdict: VerdictLabel,
  reason: string,
  requestId: string,
): Promise<components["schemas"]["AssessmentAttemptV1"]> {
  const { data, error } = await apiClient.POST(
    "/api/v1/learning/attempts/{attempt_id}/verdict-corrections",
    {
      params: { path: { attempt_id: attemptId } },
      body: {
        request_id: requestId,
        final_verdict: finalVerdict,
        reason,
      },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function getAssessment(sessionId: string): Promise<AssessmentView> {
  const { data, error } = await apiClient.GET("/api/v1/assessments/{session_id}", {
    params: { path: { session_id: sessionId } },
  });
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function cancelAssessment(
  sessionId: string,
): Promise<AssessmentView> {
  const { data, error } = await apiClient.DELETE(
    "/api/v1/assessments/{session_id}",
    {
      params: { path: { session_id: sessionId } },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function revealEvidence(
  sessionId: string,
  questionId: string,
  interaction: "hover" | "click" | "keyboard",
): Promise<AssessmentView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/assessments/{session_id}/questions/{question_id}/evidence/reveal",
    {
      params: { path: { session_id: sessionId, question_id: questionId } },
      body: { interaction },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function submitAnswer(
  sessionId: string,
  questionId: string,
  answer: string,
  requestId: string,
): Promise<AssessmentView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/assessments/{session_id}/questions/{question_id}/answers",
    {
      params: { path: { session_id: sessionId, question_id: questionId } },
      body: { answer, input_modality: "text", request_id: requestId },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function submitAppeal(
  sessionId: string,
  questionId: string,
  supplementalAnswer: string,
  requestId: string,
): Promise<AssessmentView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/assessments/{session_id}/questions/{question_id}/appeals",
    {
      params: { path: { session_id: sessionId, question_id: questionId } },
      body: {
        request_id: requestId,
        supplemental_answer: supplementalAnswer,
      },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function nextRound(
  sessionId: string,
  requestId: string,
): Promise<AssessmentView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/assessments/{session_id}/next",
    {
      params: { path: { session_id: sessionId } },
      body: { request_id: requestId },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function retryRound(
  sessionId: string,
  requestId: string,
): Promise<AssessmentView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/assessments/{session_id}/retry",
    {
      params: { path: { session_id: sessionId } },
      body: { request_id: requestId },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function getVoiceRuntimeConfig(): Promise<VoiceRuntimeConfig> {
  const { data, error } = await apiClient.GET("/api/v1/voice/config");
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

async function rawAudioRequest(
  path: string,
  audio: Blob,
  requestId: string,
  extraHeaders: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<VoiceRunView> {
  const response = await globalThis.fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": audio.type,
      "Idempotency-Key": requestId,
      ...extraHeaders,
    },
    body: audio,
    signal,
  });
  const payload: unknown = await response.json();
  if (!response.ok) {
    throw toApiRequestError(payload);
  }
  return payload as VoiceRunView;
}

export async function startVoiceRun(
  sessionId: string,
  questionId: string,
  audio: Blob,
  durationMs: number,
  requestId: string,
  signal?: AbortSignal,
): Promise<VoiceRunView> {
  return rawAudioRequest(
    `/api/v1/assessments/${encodeURIComponent(sessionId)}/questions/${encodeURIComponent(questionId)}/voice-runs`,
    audio,
    requestId,
    { "X-Client-Duration-Ms": String(durationMs) },
    signal,
  );
}

export async function cancelVoiceRequest(requestId: string): Promise<void> {
  const response = await globalThis.fetch(
    `/api/v1/voice-requests/${encodeURIComponent(requestId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    const payload: unknown = await response.json();
    throw toApiRequestError(payload);
  }
}

export async function getVoiceRun(voiceRunId: string): Promise<VoiceRunView> {
  const { data, error } = await apiClient.GET(
    "/api/v1/voice-runs/{voice_run_id}",
    { params: { path: { voice_run_id: voiceRunId } } },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function cancelVoiceRun(voiceRunId: string): Promise<VoiceRunView> {
  const { data, error } = await apiClient.DELETE(
    "/api/v1/voice-runs/{voice_run_id}",
    { params: { path: { voice_run_id: voiceRunId } } },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}

export async function retryVoiceRun(
  voiceRunId: string,
  audio: Blob,
  requestId: string,
): Promise<VoiceRunView> {
  return rawAudioRequest(
    `/api/v1/voice-runs/${encodeURIComponent(voiceRunId)}/retry`,
    audio,
    requestId,
  );
}

export async function submitVoiceRun(
  voiceRunId: string,
  editedText: string,
  requestId: string,
): Promise<VoiceRunView> {
  const { data, error } = await apiClient.POST(
    "/api/v1/voice-runs/{voice_run_id}/submit",
    {
      params: { path: { voice_run_id: voiceRunId } },
      body: { request_id: requestId, edited_text: editedText },
    },
  );
  if (error !== undefined) {
    throw toApiRequestError(error);
  }
  return data;
}
