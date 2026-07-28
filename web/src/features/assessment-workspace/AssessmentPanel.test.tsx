import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
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
  vi.useRealTimers();
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

  it("keeps evidence hidden until hover is sustained for three seconds", async () => {
    const revealedAssessment = {
      ...readyAssessment,
      question: {
        ...readyAssessment.question,
        evidence_revealed: true,
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      if (
        request.method === "POST" &&
        request.url.endsWith("/api/v1/assessments")
      ) {
        return Response.json(readyAssessment, { status: 201 });
      }
      if (
        request.method === "POST" &&
        request.url.endsWith("/evidence/reveal")
      ) {
        return Response.json(revealedAssessment);
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AssessmentPanel
        resourceId="resource-1"
        rounds={2}
        questionType="选择题"
        onClose={() => undefined}
      />,
    );
    await screen.findByRole("heading", { name: "哪项最符合材料？" });
    vi.useFakeTimers();

    fireEvent.click(screen.getByRole("radio", { name: "选项 A" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.pointerEnter(
      screen.getByRole("button", { name: "揭示本题材料证据" }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("继续悬停 3 秒查看材料")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2999);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("材料证据")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("材料证据")).toBeInTheDocument();
  });

  it("restarts the full hover countdown after the pointer leaves", async () => {
    const revealedAssessment = {
      ...readyAssessment,
      question: {
        ...readyAssessment.question,
        evidence_revealed: true,
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      if (
        request.method === "POST" &&
        request.url.endsWith("/api/v1/assessments")
      ) {
        return Response.json(readyAssessment, { status: 201 });
      }
      if (
        request.method === "POST" &&
        request.url.endsWith("/evidence/reveal")
      ) {
        return Response.json(revealedAssessment);
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AssessmentPanel
        resourceId="resource-1"
        rounds={2}
        questionType="选择题"
        onClose={() => undefined}
      />,
    );
    await screen.findByRole("heading", { name: "哪项最符合材料？" });
    vi.useFakeTimers();
    const revealButton = screen.getByRole("button", {
      name: "揭示本题材料证据",
    });

    fireEvent.pointerEnter(revealButton);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    fireEvent.pointerLeave(revealButton);
    expect(
      screen.getByText("想不起来？悬停 3 秒或点击查看材料"),
    ).toBeInTheDocument();

    fireEvent.pointerEnter(revealButton);
    expect(screen.getByText("继续悬停 3 秒查看材料")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2999);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("材料证据")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("材料证据")).toBeInTheDocument();
  });
});
