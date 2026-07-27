import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();
  onopen: (() => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(readonly url: string | URL) {
    FakeEventSource.instances.push(this);
    // Simulate connection open on next tick
    queueMicrotask(() => this.onopen?.());
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = listener as (event: MessageEvent<string>) => void;
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, payload: Record<string, unknown>) {
    const event = new MessageEvent<string>(type, { data: JSON.stringify(payload) });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }

  fail() {
    this.onerror?.(new Event("error"));
  }
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeEventSource.instances = [];
});

describe("ChatPanel", () => {
  it("creates a session on mount and renders an input area", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-1" }, { status: 201 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );

    render(<ChatPanel />);

    // Input should become enabled after session creation
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });
  });

  it("sends a message and renders the full conversation flow via SSE", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-1" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn-1" }, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel />);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "什么是事件溯源？");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // User message appears
    expect(screen.getByText("什么是事件溯源？")).toBeInTheDocument();

    // SSE stream opens
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stream = FakeEventSource.instances[0];
    expect(String(stream?.url)).toContain("/api/v1/chat/sessions/session-1/events");

    // Agent starts thinking
    await act(async () => {
      stream?.emit("chat.turn_started", {
        sequence: 1,
        type: "chat.turn_started",
        session_id: "session-1",
        data: { turn_id: "turn-1" },
      });
    });
    expect(screen.getByText("Agent 正在思考...")).toBeInTheDocument();

    // Tool call
    await act(async () => {
      stream?.emit("chat.tool_call", {
        sequence: 2,
        type: "chat.tool_call",
        session_id: "session-1",
        data: { turn_id: "turn-1", name: "search_nodes", arguments: {} },
      });
    });
    expect(screen.getByText("正在搜索材料...")).toBeInTheDocument();

    // Tool result
    await act(async () => {
      stream?.emit("chat.tool_result", {
        sequence: 3,
        type: "chat.tool_result",
        session_id: "session-1",
        data: { turn_id: "turn-1", ok: true, result: "找到 3 个节点" },
      });
    });

    // Turn ends with agent reply
    await act(async () => {
      stream?.emit("chat.turn_ended", {
        sequence: 4,
        type: "chat.turn_ended",
        session_id: "session-1",
        data: {
          turn_id: "turn-1",
          output: "事件溯源是一种将**状态变化**记录为不可变事件序列的架构模式。",
        },
      });
    });

    expect(
      await screen.findByText(/事件溯源是一种将/),
    ).toBeInTheDocument();
    // Markdown should render bold text
    expect(screen.getByText("状态变化")).toBeInTheDocument();
    expect(stream?.closed).toBe(true);
  });

  it("renders markdown with tables and lists in agent replies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-md" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn-md" }, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel />);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "列举要点");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stream = FakeEventSource.instances[0];

    const markdownReply = [
      "## 核心要点",
      "",
      "- 第一点：**不可变**事件",
      "- 第二点：事件驱动",
      "",
      "| 概念 | 说明 |",
      "|------|------|",
      "| CQRS | 命令查询分离 |",
    ].join("\n");

    await act(async () => {
      stream?.emit("chat.turn_ended", {
        sequence: 1,
        type: "chat.turn_ended",
        session_id: "session-md",
        data: { turn_id: "turn-md", output: markdownReply },
      });
    });

    // Heading rendered
    expect(screen.getByRole("heading", { level: 2, name: "核心要点" })).toBeInTheDocument();
    // List items rendered
    expect(screen.getByText("不可变")).toBeInTheDocument();
    // Table rendered
    expect(screen.getByText("CQRS")).toBeInTheDocument();
    expect(screen.getByText("命令查询分离")).toBeInTheDocument();
  });

  it("displays an error when the agent turn fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-err" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn-err" }, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel />);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "触发错误");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => {
      FakeEventSource.instances[0]?.emit("chat.error", {
        sequence: 1,
        type: "chat.error",
        session_id: "session-err",
        data: { turn_id: "turn-err", error: "ProviderError" },
      });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Agent 错误: ProviderError");
  });

  it("shows loading state during agent turn", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-load" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn-load" }, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel />);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "测试加载");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // Send button should be disabled while loading
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();

    // After turn ends, send button becomes enabled again
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => {
      FakeEventSource.instances[0]?.emit("chat.turn_ended", {
        sequence: 1,
        type: "chat.turn_ended",
        session_id: "session-load",
        data: { turn_id: "turn-load", output: "完成" },
      });
    });

    expect(screen.getByText("完成")).toBeInTheDocument();
  });

  it("calls onNavigation when a chat.navigation event is received", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-nav" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn-nav" }, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();
    const handleNavigation = vi.fn();

    render(<ChatPanel onNavigation={handleNavigation} />);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "考我几道选择题");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stream = FakeEventSource.instances[0];

    // Emit navigation event
    await act(async () => {
      stream?.emit("chat.navigation", {
        sequence: 1,
        type: "chat.navigation",
        session_id: "session-nav",
        data: {
          turn_id: "turn-nav",
          target: "assessment",
          params: { resource_id: "res-123", rounds: 3, question_type: "选择题" },
        },
      });
    });

    // onNavigation should have been called with the navigation data
    expect(handleNavigation).toHaveBeenCalledOnce();
    expect(handleNavigation).toHaveBeenCalledWith({
      target: "assessment",
      params: { resource_id: "res-123", rounds: 3, question_type: "选择题" },
    });

    // Transition hint should appear in the conversation
    expect(screen.getByText("正在为你准备考核...")).toBeInTheDocument();
  });

  it("shows reading transition hint for open_article navigation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-read" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn-read" }, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();
    const handleNavigation = vi.fn();

    render(<ChatPanel onNavigation={handleNavigation} />);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "打开文章");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stream = FakeEventSource.instances[0];

    await act(async () => {
      stream?.emit("chat.navigation", {
        sequence: 1,
        type: "chat.navigation",
        session_id: "session-read",
        data: {
          turn_id: "turn-read",
          target: "reading",
          params: { resource_id: "res-xyz" },
        },
      });
    });

    expect(handleNavigation).toHaveBeenCalledWith({
      target: "reading",
      params: { resource_id: "res-xyz" },
    });
    expect(screen.getByText("正在切换到文章阅读...")).toBeInTheDocument();
  });
});
