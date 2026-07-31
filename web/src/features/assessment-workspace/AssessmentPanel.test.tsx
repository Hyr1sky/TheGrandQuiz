import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    const startBodies: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      if (
        request.method === "POST" &&
        request.url.endsWith("/api/v1/assessments")
      ) {
        startBodies.push(await request.clone().json());
        return Response.json(readyAssessment, { status: 201 });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrictMode>
        <AssessmentPanel
          resourceId="resource-1"
          questionTypePlan={["选择题", "选择题"]}
          onClose={() => undefined}
        />
      </StrictMode>,
    );

    expect(
      await screen.findByRole("heading", { name: "哪项最符合材料？" }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(startBodies).toEqual([
      {
        resource_ids: ["resource-1"],
        rounds: 2,
        question_type_plan: ["选择题", "选择题"],
        focus: "mixed",
      },
    ]);
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
        questionTypePlan={["选择题", "选择题"]}
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
        questionTypePlan={["选择题", "选择题"]}
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

  it("cancels the backend assessment before closing the workspace", async () => {
    const cancelledAssessment = {
      ...readyAssessment,
      status: "cancelled",
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
        request.method === "DELETE" &&
        request.url.endsWith("/api/v1/assessments/assessment-1")
      ) {
        return Response.json(cancelledAssessment);
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();

    render(
      <AssessmentPanel
        resourceId="resource-1"
        questionTypePlan={["选择题", "选择题"]}
        onClose={onClose}
      />,
    );
    await screen.findByRole("heading", { name: "哪项最符合材料？" });

    fireEvent.click(screen.getByRole("button", { name: "结束考核" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    const lastRequest = fetchMock.mock.calls.at(-1)?.[0];
    expect(lastRequest).toBeInstanceOf(Request);
    expect((lastRequest as Request).method).toBe("DELETE");
  });

  it("explains a partial judgement with matched and missing points", async () => {
    const judgedAssessment = {
      ...readyAssessment,
      status: "judged",
      judgement: {
        verdict: "勉强",
        reason: "方向正确，但遗漏了连接建立成本。",
        diagnosis: "missing_key_point",
        matched_points: [
          { point_id: "short-connection", description: "指出短连接重复建立" },
        ],
        missing_points: [
          { point_id: "handshake-cost", description: "说明握手会产生额外成本" },
        ],
        concept_state: "薄弱",
        correct_answer: "HTTP/1.0 短连接会反复建立连接，因此产生额外握手成本。",
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(judgedAssessment, { status: 201 })),
    );

    render(
      <AssessmentPanel
        resourceId="resource-1"
        questionTypePlan={["简答题"]}
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText("答到了")).toBeInTheDocument();
    expect(screen.getByText("指出短连接重复建立")).toBeInTheDocument();
    expect(screen.getByText("还缺")).toBeInTheDocument();
    expect(screen.getByText("说明握手会产生额外成本")).toBeInTheDocument();
    expect(screen.getByText("方向正确，但遗漏了连接建立成本。")).toBeInTheDocument();
  });
});
