import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  it("handles /status locally without sending an LLM turn", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input));
      if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
        return Response.json({ session_id: "session-status", trace_id: "trace-status" }, { status: 201 });
      }
      if (request.url.endsWith("/api/v1/chat/sessions/session-status/status")) {
        return Response.json({
          session_id: "session-status",
          trace_id: "trace-status",
          status: "idle",
          context: {
            estimated_tokens: 2400,
            budget_tokens: 20000,
            remaining_tokens: 17600,
            estimation: "heuristic",
          },
          usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 },
        });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatPanel activeResourceLabel="Agent Runtime" />);
    const input = await screen.findByRole("textbox", { name: "发送消息" });
    await user.type(input, "/status");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("1,500 tokens")).toBeInTheDocument();
    expect(screen.getByText("17,600")).toBeInTheDocument();
    expect(screen.getByText(/上下文为本地启发式估算/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([inputValue]) => {
      const request = inputValue instanceof Request ? inputValue : new Request(String(inputValue));
      return request.url.includes("/messages");
    })).toBe(false);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("expands the compact session status on demand", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-details", trace_id: "trace-details" }, { status: 201 });
        }
        if (request.url.endsWith("/api/v1/chat/sessions/session-details/status")) {
          return Response.json({
            session_id: "session-details",
            trace_id: "trace-details",
            status: "idle",
            context: {
              estimated_tokens: 2400,
              budget_tokens: 20000,
              remaining_tokens: 17600,
              estimation: "heuristic",
            },
            usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 },
          });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    const user = userEvent.setup();

    render(<ChatPanel activeResourceLabel="Agent Runtime" />);
    const trigger = await screen.findByRole("button", { name: "查看会话状态详情" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("1,500 tokens")).not.toBeInTheDocument();

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("1,500 tokens")).toBeInTheDocument();

    await user.click(trigger);
    expect(screen.queryByText("1,500 tokens")).not.toBeInTheDocument();
  });

  it("grows the composer until its maximum height, then scrolls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-grow" }, { status: 201 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );

    render(<ChatPanel />);
    const input = await screen.findByRole("textbox", { name: "发送消息" });
    Object.defineProperty(input, "scrollHeight", { configurable: true, value: 112 });
    fireEvent.change(input, { target: { value: "第一行\n第二行\n第三行" } });
    expect(input).toHaveStyle({ height: "112px", overflowY: "hidden" });

    Object.defineProperty(input, "scrollHeight", { configurable: true, value: 260 });
    fireEvent.change(input, { target: { value: "第一行\n第二行\n第三行\n第四行" } });
    expect(input).toHaveStyle({ height: "160px", overflowY: "auto" });
  });

  it("offers honest example prompts in the empty state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request =
          input instanceof Request ? input : new Request(String(input));
        if (
          request.url.endsWith("/api/v1/chat/sessions") &&
          request.method === "POST"
        ) {
          return Response.json(
            { session_id: "session-examples" },
            { status: 201 },
          );
        }
        throw new Error(
          `Unexpected request: ${request.method} ${request.url}`,
        );
      }),
    );
    const user = userEvent.setup();

    render(<ChatPanel />);
    const example = await screen.findByRole("button", {
      name: "请结合当前材料解释核心观点",
    });
    await user.click(example);

    expect(
      screen.getByRole("textbox", { name: "发送消息" }),
    ).toHaveValue("请结合当前材料解释核心观点");
    expect(
      screen.getByRole("button", { name: "怎样查看本次运行过程？" }),
    ).toBeInTheDocument();
  });

  it("reports the durable trace id when the chat session is created", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json(
            { session_id: "session-traced", trace_id: "trace-chat-1" },
            { status: 201 },
          );
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    const handleTraceChange = vi.fn();

    render(<ChatPanel onTraceChange={handleTraceChange} />);

    await waitFor(() => {
      expect(handleTraceChange).toHaveBeenCalledWith("trace-chat-1");
    });
  });

  it("sends the active workspace resource with each user message", async () => {
    let messageBody: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-context" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          messageBody = (await request.json()) as Record<string, unknown>;
          return Response.json({ turn_id: "turn-context" }, { status: 202 });
        }
        throw new Error(`Unexpected request: ${request.method} ${request.url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel activeResourceId="resource-1" />);
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "基于当前材料考我");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(messageBody).toEqual({
        text: "基于当前材料考我",
        active_resource_id: "resource-1",
      });
    });
  });

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

  it("builds one agent bubble from deltas and finalizes it without duplication", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request =
          input instanceof Request ? input : new Request(String(input));
        if (
          request.url.endsWith("/api/v1/chat/sessions") &&
          request.method === "POST"
        ) {
          return Response.json(
            { session_id: "session-stream" },
            { status: 201 },
          );
        }
        if (
          request.url.includes("/messages") &&
          request.method === "POST"
        ) {
          return Response.json(
            { turn_id: "turn-stream" },
            { status: 202 },
          );
        }
        throw new Error(
          `Unexpected request: ${request.method} ${request.url}`,
        );
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel />);
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: "发送消息" }),
      ).toBeEnabled();
    });
    await user.type(
      screen.getByRole("textbox", { name: "发送消息" }),
      "介绍一下",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() =>
      expect(FakeEventSource.instances).toHaveLength(1),
    );
    const stream = FakeEventSource.instances[0];

    await act(async () => {
      stream?.emit("chat.message_delta", {
        sequence: 1,
        type: "chat.message_delta",
        session_id: "session-stream",
        data: { turn_id: "turn-stream", text: "正" },
      });
      stream?.emit("chat.message_delta", {
        sequence: 2,
        type: "chat.message_delta",
        session_id: "session-stream",
        data: { turn_id: "turn-stream", text: "考级" },
      });
    });
    expect(screen.getByText("正考级")).toBeInTheDocument();

    await act(async () => {
      stream?.emit("chat.turn_ended", {
        sequence: 3,
        type: "chat.turn_ended",
        session_id: "session-stream",
        data: { turn_id: "turn-stream", output: "正考级" },
      });
    });
    expect(screen.getAllByText("正考级")).toHaveLength(1);
  });

  it("resumes the SSE stream after the previous turn sequence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        if (request.url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-multi" }, { status: 201 });
        }
        if (request.url.includes("/messages") && request.method === "POST") {
          return Response.json({ turn_id: "turn" }, { status: 202 });
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

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "第一轮");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const firstStream = FakeEventSource.instances[0];
    await act(async () => {
      firstStream?.emit("chat.turn_started", {
        sequence: 1,
        type: "chat.turn_started",
        session_id: "session-multi",
        data: { turn_id: "turn-1" },
      });
      firstStream?.emit("chat.turn_ended", {
        sequence: 2,
        type: "chat.turn_ended",
        session_id: "session-multi",
        data: { turn_id: "turn-1", output: "第一轮回答" },
      });
    });

    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "第二轮");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    const secondStream = FakeEventSource.instances[1];

    expect(String(secondStream?.url)).toContain("events?after=2");
    await act(async () => {
      secondStream?.emit("chat.turn_started", {
        sequence: 3,
        type: "chat.turn_started",
        session_id: "session-multi",
        data: { turn_id: "turn-2" },
      });
      secondStream?.emit("chat.turn_ended", {
        sequence: 4,
        type: "chat.turn_ended",
        session_id: "session-multi",
        data: { turn_id: "turn-2", output: "第二轮回答" },
      });
    });

    expect(screen.getAllByText("第一轮回答")).toHaveLength(1);
    expect(screen.getAllByText("第二轮回答")).toHaveLength(1);
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

    // Running turn exposes a real cancellation control.
    expect(
      await screen.findByRole("button", { name: "停止生成" }),
    ).toBeEnabled();

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

  it("cancels the active backend turn and waits for its terminal event", async () => {
    let cancelledUrl = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request =
          input instanceof Request ? input : new Request(String(input));
        if (
          request.url.endsWith("/api/v1/chat/sessions") &&
          request.method === "POST"
        ) {
          return Response.json(
            { session_id: "session-cancel" },
            { status: 201 },
          );
        }
        if (
          request.url.includes("/messages") &&
          request.method === "POST"
        ) {
          return Response.json(
            { turn_id: "turn-cancel" },
            { status: 202 },
          );
        }
        if (
          request.url.endsWith(
            "/sessions/session-cancel/turns/turn-cancel/cancel",
          ) &&
          request.method === "POST"
        ) {
          cancelledUrl = request.url;
          return Response.json({
            turn_id: "turn-cancel",
            status: "cancelled",
          });
        }
        throw new Error(
          `Unexpected request: ${request.method} ${request.url}`,
        );
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel />);
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: "发送消息" }),
      ).toBeEnabled();
    });
    await user.type(
      screen.getByRole("textbox", { name: "发送消息" }),
      "这条发错了",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    const stopButton = await screen.findByRole("button", {
      name: "停止生成",
    });
    await user.click(stopButton);
    expect(cancelledUrl).toContain(
      "/sessions/session-cancel/turns/turn-cancel/cancel",
    );

    await waitFor(() =>
      expect(FakeEventSource.instances).toHaveLength(1),
    );
    const stream = FakeEventSource.instances[0];
    expect(stream?.closed).toBe(false);
    await act(async () => {
      stream?.emit("chat.turn_cancelled", {
        sequence: 1,
        type: "chat.turn_cancelled",
        session_id: "session-cancel",
        data: { turn_id: "turn-cancel" },
      });
    });

    expect(screen.getByText("已停止生成。")).toBeInTheDocument();
    expect(stream?.closed).toBe(true);
    expect(
      screen.queryByRole("button", { name: "停止生成" }),
    ).not.toBeInTheDocument();
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

  it("keeps the assessment launch reply alongside its lifecycle status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request =
          input instanceof Request ? input : new Request(String(input));
        if (
          request.url.endsWith("/api/v1/chat/sessions") &&
          request.method === "POST"
        ) {
          return Response.json(
            { session_id: "session-lifecycle" },
            { status: 201 },
          );
        }
        if (
          request.url.includes("/messages") &&
          request.method === "POST"
        ) {
          return Response.json(
            { turn_id: "turn-lifecycle" },
            { status: 202 },
          );
        }
        throw new Error(
          `Unexpected request: ${request.method} ${request.url}`,
        );
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    const { rerender } = render(
      <ChatPanel assessmentStatus={null} />,
    );
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: "发送消息" }),
      ).toBeEnabled();
    });
    await user.type(
      screen.getByRole("textbox", { name: "发送消息" }),
      "考我两题",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() =>
      expect(FakeEventSource.instances).toHaveLength(1),
    );

    await act(async () => {
      FakeEventSource.instances[0]?.emit("chat.navigation", {
        sequence: 1,
        type: "chat.navigation",
        session_id: "session-lifecycle",
        data: {
          turn_id: "turn-lifecycle",
          target: "assessment",
          params: { resource_id: "res-123", rounds: 2 },
        },
      });
      FakeEventSource.instances[0]?.emit("chat.turn_ended", {
        sequence: 2,
        type: "chat.turn_ended",
        session_id: "session-lifecycle",
        data: {
          turn_id: "turn-lifecycle",
          output:
            "考核已启动，请在工作面板上完成这两道选择题。完成后我会帮你查看结果小结。",
        },
      });
    });

    expect(
      screen.getByText(/完成后我会帮你查看结果小结/),
    ).toBeInTheDocument();
    expect(screen.getByText("正在为你准备考核...")).toBeInTheDocument();

    rerender(<ChatPanel assessmentStatus="completed" />);

    expect(
      screen.queryByText("正在为你准备考核..."),
    ).not.toBeInTheDocument();
    expect(screen.getByText("本轮考核已完成。")).toBeInTheDocument();
  });

  it("restores the last submitted question with ArrowUp when the composer is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request =
          input instanceof Request ? input : new Request(String(input));
        if (
          request.url.endsWith("/api/v1/chat/sessions") &&
          request.method === "POST"
        ) {
          return Response.json(
            { session_id: "session-history" },
            { status: 201 },
          );
        }
        if (
          request.url.includes("/messages") &&
          request.method === "POST"
        ) {
          return Response.json(
            { turn_id: "turn-history" },
            { status: 202 },
          );
        }
        throw new Error(
          `Unexpected request: ${request.method} ${request.url}`,
        );
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<ChatPanel />);
    const composer = screen.getByRole("textbox", { name: "发送消息" });
    await waitFor(() => expect(composer).toBeEnabled());

    await user.type(composer, "请解释事件溯源");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() =>
      expect(FakeEventSource.instances).toHaveLength(1),
    );
    await act(async () => {
      FakeEventSource.instances[0]?.emit("chat.turn_ended", {
        sequence: 1,
        type: "chat.turn_ended",
        session_id: "session-history",
        data: {
          turn_id: "turn-history",
          output: "事件溯源会把状态变化保存为事件。",
        },
      });
    });

    expect(composer).toHaveValue("");
    await user.click(composer);
    await user.keyboard("{ArrowUp}");
    expect(composer).toHaveValue("请解释事件溯源");

    await user.clear(composer);
    await user.type(composer, "正在编辑的新问题");
    await user.keyboard("{ArrowUp}");
    expect(composer).toHaveValue("正在编辑的新问题");
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
