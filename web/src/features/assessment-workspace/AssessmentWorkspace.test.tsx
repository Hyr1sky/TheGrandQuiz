import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssessmentWorkspace } from "./AssessmentWorkspace";

const resource = {
  resource_id: "resource-memory",
  url: "file://local/agent-memory.md",
  topic: "Agent 记忆",
  status: "read" as const,
  trusted: true,
  current_revision_id: "revision-memory",
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
  window.sessionStorage.clear();
});

describe("Assessment Workspace", () => {
  it("opens a real assessment setup form with material options", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        throw new Error(`Unexpected request: ${request.url}`);
      }),
    );

    render(<AssessmentWorkspace />);

    expect(await screen.findByRole("heading", { name: "开始一轮考核" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "考核材料" })).toHaveValue(
      resource.resource_id,
    );
    expect(screen.getByRole("button", { name: "生成第一题" })).toBeEnabled();
  });

  it("reveals grounded evidence and completes one question", async () => {
    const startBodies: unknown[] = [];
    const question = {
      question_id: "question-memory",
      item_id: "item-memory",
      text: "潜在记忆主要承载在哪里？",
      question_type: "选择题",
      options: ["模型内部表示", "浏览器缓存"],
      evidence_revealed: false,
      evidence: [] as string[],
    };
    const baseView = {
      session_id: "assessment-1",
      trace_id: "trace-assessment-1",
      status: "awaiting_answer" as const,
      round_index: 1,
      rounds: 1,
      question,
      judgement: null,
      error: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (request.url.endsWith("/api/v1/assessments") && request.method === "POST") {
          startBodies.push(await request.clone().json());
          return Response.json(baseView, { status: 202 });
        }
        if (request.url.endsWith("/evidence/reveal") && request.method === "POST") {
          return Response.json({
            ...baseView,
            question: {
              ...question,
              evidence_revealed: true,
              evidence: ["潜在记忆以隐式形式承载在模型内部表示中。"],
            },
          });
        }
        if (request.url.endsWith("/answers") && request.method === "POST") {
          return Response.json(
            {
              ...baseView,
              status: "completed",
              judgement: {
                verdict: "对",
                reason: "",
                concept_state: null,
                correct_answer: null,
              },
            },
            { status: 202 },
          );
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    const user = userEvent.setup();

    render(<AssessmentWorkspace />);
    await screen.findByRole("heading", { name: "开始一轮考核" });
    await user.click(screen.getByRole("button", { name: "生成第一题" }));

    expect(
      await screen.findByRole("heading", { name: "潜在记忆主要承载在哪里？" }),
    ).toBeInTheDocument();
    expect(startBodies).toEqual([
      {
        resource_ids: [resource.resource_id],
        rounds: 3,
        question_type: null,
        focus: "mixed",
      },
    ]);
    const evidence = screen.getByRole("button", { name: "揭示本题材料证据" });
    await user.hover(evidence);
    expect(
      await screen.findByText("潜在记忆以隐式形式承载在模型内部表示中。"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "模型内部表示" }));
    await user.click(screen.getByRole("button", { name: "提交答案" }));

    expect(await screen.findByText("判断：对")).toBeInTheDocument();
    expect(screen.getByText("本轮完成")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "开始新一轮" }));
    expect(screen.getByRole("heading", { name: "开始一轮考核" })).toBeInTheDocument();
  });

  it("resumes the current question after a page refresh without starting again", async () => {
    window.sessionStorage.setItem("grandquiz.assessment.session_id", "assessment-resume");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input));
      if (request.url.endsWith("/api/v1/resources")) {
        return Response.json({ items: [resource] });
      }
      if (request.url.endsWith("/api/v1/assessments/assessment-resume")) {
        return Response.json({
          session_id: "assessment-resume",
          trace_id: "trace-resume",
          status: "awaiting_answer",
          round_index: 2,
          rounds: 3,
          question: {
            question_id: "question-resume",
            item_id: "item-memory",
            text: "刷新后继续回答哪一道题？",
            question_type: "简答",
            options: [],
            evidence_revealed: false,
            evidence: [],
          },
          judgement: null,
          error: null,
        });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AssessmentWorkspace />);

    expect(
      await screen.findByRole("heading", { name: "刷新后继续回答哪一道题？" }),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("explains when the selected material has no assessable knowledge", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (request.url.endsWith("/api/v1/assessments") && request.method === "POST") {
          return Response.json(
            {
              session_id: "assessment-refused",
              trace_id: "trace-refused",
              status: "refused",
              round_index: 1,
              rounds: 3,
              question: null,
              judgement: null,
              error: "当前选择的材料中没有可用于考核的知识点。",
            },
            { status: 202 },
          );
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    const user = userEvent.setup();

    render(<AssessmentWorkspace />);
    await screen.findByRole("heading", { name: "开始一轮考核" });
    await user.click(screen.getByRole("button", { name: "生成第一题" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "当前选择的材料中没有可用于考核的知识点。",
    );
  });
});
