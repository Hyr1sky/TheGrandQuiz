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

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  FakeEventSource.instances = [];
});

describe("Article Workspace", () => {
  it("loads the first material, its outline, and the selected node", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith("/api/v1/resources")) {
        return Response.json({ items: [resource] });
      }
      if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
        return Response.json(outline);
      }
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
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(screen.getByText("正在打开本地材料…")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Agent Runtime" })).toBeInTheDocument();
    await screen.findByRole("button", { name: /Events/ }).then((button) => button.click());
    expect(
      await screen.findByText("事件是系统的数据脊柱，所有状态变化都写入事件流。"),
    ).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });

  it("switches the selected visual language between dark and light modes", async () => {
    localStorage.setItem("grandquiz-theme", "dark");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
          return Response.json(outline);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();

    render(<App />);

    const toggle = await screen.findByRole("button", { name: "切换至亮色模式" });
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    await user.click(toggle);
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(localStorage.getItem("grandquiz-theme")).toBe("light");
  });

  it("streams a grounded answer and deliberately reveals its exact evidence", async () => {
    const run = {
      run_id: "run-1",
      trace_id: "trace-1",
      status: "queued",
      result: null,
      error: null,
    };
    const completedRun = {
      ...run,
      status: "succeeded",
      result: {
        status: "answered",
        answer: "失败后继续执行会让部分副作用依赖不完整状态，破坏因果一致性。",
        citations: [
          {
            resource_id: resource.resource_id,
            revision_id: "revision-1",
            node_id: "events",
            section_path: "Runtime > Events",
            start_offset: 10,
            end_offset: 21,
            quote: "事件是系统的数据脊柱",
            context_start: 0,
            context_end: 32,
            context: "Events\n事件是系统的数据脊柱，所有状态变化都写入事件流。",
          },
        ],
        searched_node_ids: ["events"],
        read_node_ids: ["events"],
        resource_ids: [resource.resource_id],
        metrics: {
          candidate_nodes: 1,
          read_nodes: 1,
          read_chars: 32,
          model_calls: 1,
          prompt_tokens: 100,
          completion_tokens: 20,
          total_tokens: 120,
          max_prompt_tokens: 100,
        },
        detail: null,
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input));
      const url = request.url;
      if (url.endsWith("/api/v1/resources")) {
        return Response.json({ items: [resource] });
      }
      if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
        return Response.json(outline);
      }
      if (url.endsWith(`/api/v1/resources/${resource.resource_id}/questions`)) {
        return Response.json(run, { status: 202 });
      }
      if (url.endsWith("/api/v1/runs/run-1")) {
        return Response.json(completedRun);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.type(
      screen.getByRole("textbox", { name: "针对当前材料的问题" }),
      "为什么 durable processor 失败必须阻断当前 turn？",
    );
    await user.click(screen.getByRole("button", { name: "向材料提问" }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stream = FakeEventSource.instances[0];
    expect(String(stream?.url)).toContain("/api/v1/runs/run-1/events?after=0");
    const events = [
      ["search.completed", 3],
      ["node.read", 4],
      ["citation.resolved", 5],
      ["answer.completed", 6],
      ["run.succeeded", 7],
    ] as const;
    await act(async () => {
      for (const [type, sequence] of events) {
        stream?.emit(type, {
          sequence,
          type,
          run_id: "run-1",
          trace_id: "trace-1",
          data: {},
        });
      }
    });

    expect(
      await screen.findByText("失败后继续执行会让部分副作用依赖不完整状态，破坏因果一致性。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Runtime > Events/ })).toBeInTheDocument();
    expect(screen.queryByText("事件是系统的数据脊柱")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "揭示证据" }));
    expect(screen.getByText("事件是系统的数据脊柱")).toBeInTheDocument();
    expect(stream?.closed).toBe(true);
  });

  it("changes the exact material scope before reading or asking", async () => {
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
        const url = input instanceof Request ? input.url : String(input);
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

  it("shows a redacted provider failure with its trace id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        const url = request.url;
        if (url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
          return Response.json(outline);
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/questions`)) {
          return Response.json(
            {
              run_id: "run-failed",
              trace_id: "trace-failed",
              status: "queued",
              result: null,
              error: null,
            },
            { status: 202 },
          );
        }
        if (url.endsWith("/api/v1/runs/run-failed")) {
          return Response.json({
            run_id: "run-failed",
            trace_id: "trace-failed",
            status: "failed",
            result: null,
            error: {
              code: "run_failed",
              message: "运行失败，请通过 trace_id 查看详情",
              retryable: true,
              trace_id: "trace-failed",
            },
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.type(screen.getByRole("textbox"), "事件失败后怎么办？");
    await user.click(screen.getByRole("button", { name: "向材料提问" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => {
      FakeEventSource.instances[0]?.emit("run.failed", {
        sequence: 3,
        type: "run.failed",
        run_id: "run-failed",
        trace_id: "trace-failed",
        data: { code: "run_failed" },
      });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "运行失败，请通过 trace_id 查看详情",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("trace-failed");
  });

  it("cancels an active run without discarding its trace identity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        const url = request.url;
        if (url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
          return Response.json(outline);
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/questions`)) {
          return Response.json(
            {
              run_id: "run-cancel",
              trace_id: "trace-cancel",
              status: "queued",
              result: null,
              error: null,
            },
            { status: 202 },
          );
        }
        if (url.endsWith("/api/v1/runs/run-cancel/cancel")) {
          return Response.json({
            run_id: "run-cancel",
            trace_id: "trace-cancel",
            status: "cancelled",
            result: null,
            error: null,
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.type(screen.getByRole("textbox"), "停止这个问题");
    await user.click(screen.getByRole("button", { name: "向材料提问" }));
    await user.click(await screen.findByRole("button", { name: "取消运行" }));

    expect(await screen.findByRole("status")).toHaveTextContent("运行已取消");
    expect(screen.getByRole("status")).toHaveTextContent("trace-cancel");
    expect(FakeEventSource.instances[0]?.closed).toBe(true);
  });

  it("resumes a disconnected event stream after the last received sequence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        const url = request.url;
        if (url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
          return Response.json(outline);
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/questions`)) {
          return Response.json(
            {
              run_id: "run-resume",
              trace_id: "trace-resume",
              status: "queued",
              result: null,
              error: null,
            },
            { status: 202 },
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.type(screen.getByRole("textbox"), "继续上次的事件流");
    await user.click(screen.getByRole("button", { name: "向材料提问" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => {
      FakeEventSource.instances[0]?.emit("search.completed", {
        sequence: 3,
        type: "search.completed",
        run_id: "run-resume",
        trace_id: "trace-resume",
        data: { candidate_count: 2 },
      });
    });

    vi.useFakeTimers();
    await act(async () => {
      FakeEventSource.instances[0]?.fail();
    });
    expect(screen.getByRole("status")).toHaveTextContent("实时连接已中断");
    await act(async () => {
      vi.advanceTimersByTime(750);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(String(FakeEventSource.instances[1]?.url)).toContain("after=3");
    vi.useRealTimers();
  });

  it("presents no evidence as a successful fail-safe result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(String(input));
        const url = request.url;
        if (url.endsWith("/api/v1/resources")) {
          return Response.json({ items: [resource] });
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/outline`)) {
          return Response.json(outline);
        }
        if (url.endsWith(`/api/v1/resources/${resource.resource_id}/questions`)) {
          return Response.json(
            {
              run_id: "run-empty",
              trace_id: "trace-empty",
              status: "queued",
              result: null,
              error: null,
            },
            { status: 202 },
          );
        }
        if (url.endsWith("/api/v1/runs/run-empty")) {
          return Response.json({
            run_id: "run-empty",
            trace_id: "trace-empty",
            status: "succeeded",
            error: null,
            result: {
              status: "no_evidence",
              answer: "材料中没有足够证据回答该问题。",
              citations: [],
              searched_node_ids: [],
              read_node_ids: [],
              resource_ids: [resource.resource_id],
              metrics: {
                candidate_nodes: 0,
                read_nodes: 0,
                read_chars: 0,
                model_calls: 0,
                prompt_tokens: 0,
                completion_tokens: 0,
                total_tokens: 0,
                max_prompt_tokens: 0,
              },
              detail: "稀疏搜索没有返回可读原文证据",
            },
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Agent Runtime" });
    await user.type(screen.getByRole("textbox"), "材料没有写什么？");
    await user.click(screen.getByRole("button", { name: "向材料提问" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await act(async () => {
      FakeEventSource.instances[0]?.emit("run.succeeded", {
        sequence: 3,
        type: "run.succeeded",
        run_id: "run-empty",
        trace_id: "trace-empty",
        data: {},
      });
    });

    expect(await screen.findByText("材料中没有足够证据，已停止生成答案。")).toBeInTheDocument();
    expect(screen.queryByText("回答（基于本文）")).not.toBeInTheDocument();
    expect(screen.queryByText("材料中没有足够证据回答该问题。")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "揭示证据" })).not.toBeInTheDocument();
  });
});
