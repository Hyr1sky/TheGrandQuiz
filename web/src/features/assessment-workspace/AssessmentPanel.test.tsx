import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssessmentPanel } from "./AssessmentPanel";

vi.mock("./VoiceAnswerControl", () => ({
  VoiceAnswerControl: ({
    onCaptureStart,
    onReviewable,
  }: {
    onCaptureStart: () => void;
    onReviewable: (run: Record<string, unknown>) => void;
  }) => (
    <div>
      <button type="button" onClick={onCaptureStart}>
        模拟开始录音
      </button>
      <button
        type="button"
        onClick={() =>
          onReviewable({
            status: "reviewable",
            voice_run_id: "voice-1",
            reviewable_transcript: "语音识别草稿",
          })
        }
      >
        模拟识别完成
      </button>
    </div>
  ),
}));

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
  it("asks before replacing a text draft with a voice transcript", async () => {
    const openAssessment = {
      ...readyAssessment,
      question: {
        ...readyAssessment.question,
        question_type: "开放",
        options: [],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(openAssessment, { status: 202 })),
    );
    render(
      <AssessmentPanel
        resourceId="resource-1"
        questionTypePlan={["简答题"]}
        onClose={() => undefined}
      />,
    );

    const answer = await screen.findByPlaceholderText("先给出自己的理解...");
    fireEvent.change(answer, { target: { value: "我的文字草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "模拟开始录音" }));
    fireEvent.click(screen.getByRole("button", { name: "模拟识别完成" }));

    expect(answer).toHaveValue("我的文字草稿");
    expect(screen.getByRole("button", { name: "替换现有回答" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "追加到回答" }));
    expect(answer).toHaveValue("我的文字草稿\n\n语音识别草稿");
  });

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

  it("groups judgement actions and shows the dynamic next-round number", async () => {
    const judgedAssessment = {
      ...readyAssessment,
      status: "judged",
      judgement: {
        verdict: "对",
        reason: "回答正确。",
        diagnosis: "complete",
        matched_points: [],
        missing_points: [],
        concept_state: null,
        correct_answer: null,
      },
      appeal: {
        status: "available",
        supplemental_answer: null,
        original_verdict: "对",
        final_verdict: null,
        reason: null,
      },
    };
    const preparingSecondRound = {
      ...judgedAssessment,
      status: "preparing",
      round_index: 2,
      question: null,
      judgement: null,
      appeal: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (
          request.method === "POST" &&
          request.url.endsWith("/api/v1/assessments")
        ) {
          return Response.json(judgedAssessment, { status: 201 });
        }
        if (request.method === "POST" && request.url.endsWith("/next")) {
          return Response.json(preparingSecondRound, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );

    render(
      <AssessmentPanel
        resourceId="resource-1"
        questionTypePlan={["选择题", "选择题"]}
        onClose={() => undefined}
      />,
    );

    const appealButton = await screen.findByRole("button", {
      name: "补充说明 / 判卷有异议",
    });
    const nextButton = screen.getByRole("button", { name: "下一题" });
    expect(appealButton.parentElement).toBe(nextButton.parentElement);
    expect(appealButton.parentElement).toHaveClass("assessment-panel__actions");

    fireEvent.click(nextButton);

    expect(await screen.findByText("正在生成第 2 题")).toBeInTheDocument();
    expect(screen.queryByText("正在生成第 1 题")).not.toBeInTheDocument();
  });

  it("submits one supplemental explanation and renders the revised judgement", async () => {
    const judgedAssessment = {
      ...readyAssessment,
      status: "completed",
      attempt_id: "attempt-1",
      question: {
        ...readyAssessment.question,
        question_type: "简答题",
        options: [],
      },
      judgement: {
        verdict: "错",
        reason: "没有说明模型内部表示。",
        diagnosis: "wrong_focus",
        matched_points: [],
        missing_points: [
          { point_id: "location", description: "指出位于模型内部表示" },
        ],
        concept_state: "薄弱",
        correct_answer: "潜在记忆位于模型内部表示。",
      },
      appeal: {
        status: "available",
        supplemental_answer: null,
        original_verdict: "错",
        final_verdict: null,
        reason: null,
      },
    };
    const resolvedAssessment = {
      ...judgedAssessment,
      judgement: {
        ...judgedAssessment.judgement,
        verdict: "对",
        reason: "结合补充说明，已经覆盖位置要点。",
        diagnosis: "complete",
        matched_points: [
          { point_id: "location", description: "指出位于模型内部表示" },
        ],
        missing_points: [],
        concept_state: null,
      },
      appeal: {
        status: "resolved",
        supplemental_answer: "我指的是模型内部的隐式表示。",
        original_verdict: "错",
        final_verdict: "对",
        reason: "结合补充说明，已经覆盖位置要点。",
      },
    };
    const appealBodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/assessments")) {
          return Response.json(judgedAssessment, { status: 202 });
        }
        if (request.url.endsWith("/appeals") && request.method === "POST") {
          appealBodies.push(await request.clone().json());
          return Response.json(resolvedAssessment, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );

    render(
      <AssessmentPanel
        resourceId="resource-1"
        questionTypePlan={["简答题"]}
        onClose={() => undefined}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "补充说明 / 判卷有异议" }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "补充说明" }), {
      target: { value: "我指的是模型内部的隐式表示。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交补充并重判" }));

    expect(
      await screen.findByText("结合补充说明，已经覆盖位置要点。"),
    ).toBeInTheDocument();
    expect(screen.getByText("原判：错；重判：对")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "补充说明 / 判卷有异议" }))
      .not.toBeInTheDocument();
    expect(appealBodies).toEqual([
      expect.objectContaining({
        supplemental_answer: "我指的是模型内部的隐式表示。",
      }),
    ]);
  });

  it("cancels an in-flight appeal when the completed assessment is closed", async () => {
    const gradingAssessment = {
      ...readyAssessment,
      status: "completed",
      appeal: {
        status: "grading",
        supplemental_answer: "补充说明",
        original_verdict: "错",
        final_verdict: null,
        reason: null,
      },
    };
    const cancelledAssessment = {
      ...gradingAssessment,
      appeal: { ...gradingAssessment.appeal, status: "cancelled" },
    };
    const onClose = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input));
      if (request.method === "POST" && request.url.endsWith("/api/v1/assessments")) {
        return Response.json(gradingAssessment, { status: 202 });
      }
      if (request.method === "DELETE") {
        return Response.json(cancelledAssessment);
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AssessmentPanel
        resourceId="resource-1"
        questionTypePlan={["简答题"]}
        onClose={onClose}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "结束考核" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(
      fetchMock.mock.calls.some(([input]) => {
        const request = input instanceof Request ? input : new Request(String(input));
        return (
          request.method === "DELETE" &&
          request.url.endsWith("/api/v1/assessments/assessment-1")
        );
      }),
    ).toBe(true);
  });
});
