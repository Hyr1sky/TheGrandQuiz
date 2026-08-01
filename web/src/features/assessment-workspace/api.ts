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
