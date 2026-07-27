import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../../app/App";

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
    {
      node_id: "events",
      revision_id: "revision-1",
      parent_node_id: "runtime",
      kind: "section",
      ordinal: 1,
      depth: 1,
      title: "Events",
      section_path: "Runtime > Events",
      synthetic: false,
    },
  ],
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(readonly url: string | URL) {
    FakeEventSource.instances.push(this);
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

/** Standard fetch mock that handles resources, outline, and chat session creation. */
function baseFetchMock(extras?: (url: string, request: Request) => Response | null) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const request = input instanceof Request ? input : new Request(String(input));
    const url = request.url;
    if (url.endsWith("/api/v1/resources")) {
      return Response.json({ items: [resource] });
    }
    if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
      return Response.json(outline);
    }
    if (url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
      return Response.json({ session_id: "session-test" }, { status: 201 });
    }
    if (extras) {
      const result = extras(url, request);
      if (result !== null) {
        return result;
      }
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

describe("Article Workspace", () => {
  it("loads the first material, its outline, and the selected node", async () => {
    const fetchMock = baseFetchMock((url) => {
      if (url.includes("/nodes/events")) {
        return Response.json({
          resource_id: resource.resource_id,
          revision_id: "revision-1",
          node_id: "events",
          section_path: "Runtime > Events",
          start_offset: 10,
          end_offset: 46,
          content: "事件是系统的数据脊柱，所有状态变化都写入事件流。",
          has_more: false,
          untrusted: true,
        });
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Agent Runtime" })).toBeInTheDocument();
    await screen.findByRole("button", { name: /Events/ }).then((button) => button.click());
    expect(
      await screen.findByText("事件是系统的数据脊柱，所有状态变化都写入事件流。"),
    ).toBeInTheDocument();
  });

  it("switches the selected visual language between dark and light modes", async () => {
    localStorage.setItem("grandquiz-theme", "dark");
    vi.stubGlobal("fetch", baseFetchMock());
    const user = userEvent.setup();

    render(<App />);

    const toggle = await screen.findByRole("button", { name: "切换至亮色模式" });
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    await user.click(toggle);
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(localStorage.getItem("grandquiz-theme")).toBe("light");
  });

  it("sends a chat message and displays the agent reply via SSE", async () => {
    vi.stubGlobal("fetch", baseFetchMock((url, request) => {
      if (url.includes("/api/v1/chat/sessions/session-test/messages") && request.method === "POST") {
        return Response.json({ turn_id: "turn-1" }, { status: 202 });
      }
      return null;
    }));
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });

    const chatInput = screen.getByRole("textbox", { name: "发送消息" });
    await user.type(chatInput, "为什么 durable processor 失败必须阻断当前 turn？");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stream = FakeEventSource.instances[0];
    expect(String(stream?.url)).toContain("/api/v1/chat/sessions/session-test/events?after=0");

    await act(async () => {
      stream?.emit("chat.turn_started", {
        sequence: 1,
        type: "chat.turn_started",
        session_id: "session-test",
        data: { turn_id: "turn-1" },
      });
    });
    expect(screen.getByText("Agent 正在思考...")).toBeInTheDocument();

    await act(async () => {
      stream?.emit("chat.tool_call", {
        sequence: 2,
        type: "chat.tool_call",
        session_id: "session-test",
        data: { turn_id: "turn-1", name: "search_nodes", arguments: {} },
      });
    });
    expect(screen.getByText("正在搜索材料...")).toBeInTheDocument();

    await act(async () => {
      stream?.emit("chat.turn_ended", {
        sequence: 3,
        type: "chat.turn_ended",
        session_id: "session-test",
        data: { turn_id: "turn-1", output: "失败后继续执行会让部分副作用依赖不完整状态，破坏因果一致性。" },
      });
    });

    expect(
      await screen.findByText("失败后继续执行会让部分副作用依赖不完整状态，破坏因果一致性。"),
    ).toBeInTheDocument();
    expect(stream?.closed).toBe(true);
  });

  it("changes the exact material scope before reading", async () => {
    const secondResource = {
      ...resource,
      resource_id: "resource-2",
      topic: "Recovery Design",
      url: "file://local/recovery.md",
      current_revision_id: "revision-2",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        const url = request.url;
        if (url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource, secondResource] });
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
          return Response.json(outline);
        }
        if (url.endsWith(`/api/v1/resources/${secondResource.resource_id}/outline`)) {
          return Response.json({
            resource_id: secondResource.resource_id,
            nodes: [
              {
                ...outline.nodes[0],
                node_id: "recovery",
                revision_id: "revision-2",
                title: "Recovery",
                section_path: "Recovery",
              },
            ],
          });
        }
        if (url.endsWith("/api/v1/chat/sessions") && request.method === "POST") {
          return Response.json({ session_id: "session-test" }, { status: 201 });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.selectOptions(screen.getByRole("combobox", { name: "当前材料" }), "resource-2");

    expect(
      await screen.findByRole("heading", { name: "Recovery Design" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Recovery/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Events/ })).not.toBeInTheDocument();
  });

  it("shows a chat error when the agent turn fails", async () => {
    vi.stubGlobal("fetch", baseFetchMock((url, request) => {
      if (url.includes("/api/v1/chat/sessions/session-test/messages") && request.method === "POST") {
        return Response.json({ turn_id: "turn-err" }, { status: 202 });
      }
      return null;
    }));
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "事件失败后怎么办？");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => {
      FakeEventSource.instances[0]?.emit("chat.error", {
        sequence: 1,
        type: "chat.error",
        session_id: "session-test",
        data: { turn_id: "turn-err", error: "ProviderError" },
      });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Agent 错误: ProviderError");
    expect(FakeEventSource.instances[0]?.closed).toBe(true);
  });

  it("resumes a disconnected chat event stream after the last received sequence", async () => {
    vi.stubGlobal("fetch", baseFetchMock((url, request) => {
      if (url.includes("/api/v1/chat/sessions/session-test/messages") && request.method === "POST") {
        return Response.json({ turn_id: "turn-resume" }, { status: 202 });
      }
      return null;
    }));
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "继续上次的事件流");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => {
      FakeEventSource.instances[0]?.emit("chat.tool_call", {
        sequence: 2,
        type: "chat.tool_call",
        session_id: "session-test",
        data: { turn_id: "turn-resume", name: "search_nodes", arguments: {} },
      });
    });

    vi.useFakeTimers();
    await act(async () => {
      FakeEventSource.instances[0]?.fail();
    });
    expect(screen.getByText("实时连接已中断，正在重新连接...")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(750);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(String(FakeEventSource.instances[1]?.url)).toContain("after=2");
    vi.useRealTimers();
  });
});
