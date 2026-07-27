import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

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
        { session_id: "session-test" },
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
