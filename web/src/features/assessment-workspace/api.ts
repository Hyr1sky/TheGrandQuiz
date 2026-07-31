import { apiClient, toApiRequestError } from "../../shared/api/client";
import type { components } from "../../shared/api/generated/schema";

export type AssessmentView = components["schemas"]["AssessmentView"];

export type AssessmentStartPlan =
  | {
      rounds: number;
      questionType: string | null;
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
