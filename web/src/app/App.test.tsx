import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import "../styles.css";

const resource = {
  resource_id: "resource-1",
  url: "file://local/runtime.md",
  topic: "Agent Runtime",
  status: "read",
  trusted: true,
  current_revision_id: "revision-1",
};

const outline = {
  resource_id: resource.resource_id,
  nodes: [
    {
      node_id: "runtime",
      revision_id: "revision-1",
      parent_node_id: null,
      kind: "section",
      ordinal: 0,
      depth: 0,
      title: "Runtime",
      section_path: "Runtime",
      synthetic: false,
    },
  ],
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<
    string,
    Array<(event: MessageEvent<string>) => void>
  >();
  onopen: (() => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(readonly url: string | URL) {
    FakeEventSource.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }

  addEventListener(
    type: string,
    listener: EventListenerOrEventListenerObject,
  ) {
    const callback = listener as (event: MessageEvent<string>) => void;
    this.listeners.set(type, [
      ...(this.listeners.get(type) ?? []),
      callback,
    ]);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, payload: Record<string, unknown>) {
    const event = new MessageEvent<string>(type, {
      data: JSON.stringify(payload),
    });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

function baseFetchMock() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const request =
      input instanceof Request ? input : new Request(String(input));
    const url = request.url;
    if (url.endsWith("/api/v1/resources")) {
      return Response.json({ items: [resource] });
    }
    if (
      url.endsWith(
        `/api/v1/resources/${resource.resource_id}/outline`,
      )
    ) {
      return Response.json(outline);
    }
    if (
      url.endsWith("/api/v1/chat/sessions") &&
      request.method === "POST"
    ) {
      return Response.json(
        { session_id: "session-test", trace_id: "trace-chat-test" },
        { status: 201 },
      );
    }
    throw new Error(`Unexpected request: ${request.method} ${url}`);
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  FakeEventSource.instances = [];
});

describe("Sidebar context switching", () => {
  it("opens the live observatory from the compass status bar", async () => {
    const fetchMock = baseFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      if (
        request.url.endsWith(
          "/api/v1/observability/traces/trace-chat-test",
        )
      ) {
        return Response.json({
          summary: {
            trace_id: "trace-chat-test",
            status: "idle",
            event_count: 0,
            model_calls: 0,
            tool_calls: 0,
            error_count: 0,
            recovery_count: 0,
            total_tokens: 0,
            started_at: null,
            updated_at: null,
            latency_ms: null,
          },
          spans: [],
          events: [],
        });
      }
      return baseFetchMock()(input);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    await user.click(
      screen.getByRole("button", { name: "打开运行观测" }),
    );

    expect(
      await screen.findByRole("dialog", { name: "运行观测" }),
    ).toBeInTheDocument();
  });

  it("keeps the observatory map decorative and outside the interaction layer", async () => {
    vi.stubGlobal("fetch", baseFetchMock());

    const { container } = render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    const backdrop = container.querySelector(".star-map-backdrop");
    expect(backdrop).toHaveAttribute("aria-hidden", "true");
    expect(backdrop).toHaveAttribute("data-visual", "observatory");
  });

  it("shows document outline by default in reading mode", async () => {
    vi.stubGlobal("fetch", baseFetchMock());

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    // Sidebar should show outline
    expect(
      screen.getByRole("navigation", { name: "文档大纲" }),
    ).toBeInTheDocument();
    // Toggle button should say "大纲"
    expect(
      screen.getByRole("button", { name: "切换到考核进度" }),
    ).toBeInTheDocument();
    // Outline item should be present
    expect(
      screen.getByRole("button", { name: /Runtime/ }),
    ).toBeInTheDocument();
  });

  it("renders selected document nodes as Markdown", async () => {
    const longFlow =
      "fetch_resource -> parse_document -> build_revision -> extract_nodes -> index_search -> resolve_evidence";
    const fetchMock = baseFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      if (
        request.url.includes(
          `/api/v1/resources/${resource.resource_id}/nodes/runtime`,
        )
      ) {
        return Response.json({
          resource_id: resource.resource_id,
          revision_id: "revision-1",
          node_id: "runtime",
          section_path: "Runtime",
          start_offset: 0,
          end_offset: 128,
          content:
            `# Runtime\n\n## 核心结构\n\n- 事件脊柱\n- 确定性 workflow\n\n![超宽流程图](https://example.com/wide-diagram.png)\n\n\`\`\`text\n${longFlow}\n\`\`\`\n\n| 模块 | 作用 | 输入 | 输出 | 状态 | 恢复 | 观测 | 评测 |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n| trace | 回放 | 一段很长的事件输入 | 一段很长的事件输出 | completed | checkpoint | sequence | replay |`,
          has_more: false,
          untrusted: true,
        });
      }
      return baseFetchMock()(input);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    await user.click(screen.getByRole("button", { name: /Runtime/ }));

    expect(
      await screen.findByRole("heading", {
        level: 2,
        name: "核心结构",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("list")).toHaveTextContent("事件脊柱");
    expect(screen.getByRole("table")).toHaveTextContent("trace");

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("超宽流程图");
    expect(screen.getByRole("note")).toHaveTextContent(
      "https://example.com/wide-diagram.png",
    );
    const table = screen.getByRole("table");
    const codeBlock = screen.getByText(longFlow).closest("pre");
    expect(getComputedStyle(table).display).toBe("block");
    expect(getComputedStyle(table).overflowX).toBe("auto");
    expect(codeBlock).not.toBeNull();
    expect(getComputedStyle(codeBlock as HTMLElement).overflowX).toBe(
      "auto",
    );
  });

  it("switches sidebar to progress view when user clicks toggle", async () => {
    vi.stubGlobal("fetch", baseFetchMock());
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    // Click toggle to switch to progress view
    await user.click(
      screen.getByRole("button", { name: "切换到考核进度" }),
    );

    // Sidebar label should change
    expect(
      screen.getByRole("navigation", { name: "考核进度" }),
    ).toBeInTheDocument();
    // Toggle button should now say to switch back to outline
    expect(
      screen.getByRole("button", { name: "切换到文档大纲" }),
    ).toBeInTheDocument();
    // Progress panel should show preparing state
    expect(screen.getByText("考核准备中...")).toBeInTheDocument();
  });

  it("auto-switches sidebar to progress when workspace changes to assessment", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request =
          input instanceof Request
            ? input
            : new Request(String(input));
        const url = request.url;
        if (url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
          return Response.json(outline);
        }
        if (url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-auto" }, { status: 201 });
        }
        if (url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn-auto" }, { status: 202 });
        }
        if (url.endsWith("/api/v1/assessments") && request.method === "POST") {
          return Response.json(
            {
              session_id: "assess-1",
              status: "preparing",
              rounds: 3,
              round_index: 1,
              trace_id: "trace-1",
              question: null,
              judgement: null,
              error: null,
            },
            { status: 201 },
          );
        }
        throw new Error(`Unexpected request: ${request.method} ${url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    // Start in outline mode
    expect(
      screen.getByRole("navigation", { name: "文档大纲" }),
    ).toBeInTheDocument();

    // Send a message to trigger navigation
    await user.type(
      screen.getByRole("textbox", { name: "发送消息" }),
      "考我几题",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() =>
      expect(FakeEventSource.instances).toHaveLength(1),
    );
    const stream = FakeEventSource.instances[0];

    // Emit navigation event to switch to assessment
    await act(async () => {
      stream?.emit("chat.navigation", {
        sequence: 1,
        type: "chat.navigation",
        session_id: "session-auto",
        data: {
          turn_id: "turn-auto",
          target: "assessment",
          params: {
            resource_id: "resource-1",
            rounds: 3,
            question_type: null,
          },
        },
      });
    });

    // Sidebar should auto-switch to progress
    await waitFor(() => {
      expect(
        screen.getByRole("navigation", { name: "考核进度" }),
      ).toBeInTheDocument();
    });
  });

  it("preserves manual override until next workspace change", async () => {
    vi.stubGlobal("fetch", baseFetchMock());
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    // Manually switch to progress view
    await user.click(
      screen.getByRole("button", { name: "切换到考核进度" }),
    );
    expect(
      screen.getByRole("navigation", { name: "考核进度" }),
    ).toBeInTheDocument();

    // Toggle back to outline
    await user.click(
      screen.getByRole("button", { name: "切换到文档大纲" }),
    );
    expect(
      screen.getByRole("navigation", { name: "文档大纲" }),
    ).toBeInTheDocument();
  });
});

describe("Compass navigation footer", () => {
  it("shows reading state and material name in footer", async () => {
    vi.stubGlobal("fetch", baseFetchMock());

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    const footer = screen.getByRole("contentinfo", { name: "状态栏" });
    expect(footer).toBeInTheDocument();
    expect(footer).toHaveTextContent("阅读");
    expect(footer).toHaveTextContent("Agent Runtime");
  });
});
