import { StrictMode } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssessmentPanel } from "./AssessmentPanel";

const readyAssessment = {
  session_id: "assessment-1",
  status: "awaiting_answer",
  rounds: 2,
  round_index: 1,
  trace_id: "trace-1",
  question: {
    question_id: "question-1",
    question_type: "选择题",
    text: "哪项最符合材料？",
    options: ["选项 A", "选项 B"],
    evidence: ["材料证据"],
    evidence_revealed: false,
  },
  judgement: null,
  error: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AssessmentPanel", () => {
  it("shows the generated question when mounted in React StrictMode", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      if (
        request.method === "POST" &&
        request.url.endsWith("/api/v1/assessments")
      ) {
        return Response.json(readyAssessment, { status: 201 });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrictMode>
        <AssessmentPanel
          resourceId="resource-1"
          rounds={2}
          questionType="选择题"
          onClose={() => undefined}
        />
      </StrictMode>,
    );

    expect(
      await screen.findByRole("heading", { name: "哪项最符合材料？" }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
