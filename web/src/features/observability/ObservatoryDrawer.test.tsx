import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObservatoryDrawer } from "./ObservatoryDrawer";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<
    string,
    Array<(event: MessageEvent<string>) => void>
  >();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

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

  close() {}
}

const snapshot = {
  schema_version: 1,
  trace_id: "trace-1",
  status: "completed",
  started_at: 1,
  ended_at: 1.12,
  workflow_kind: "assessment",
  summary: {
    model_calls: 1,
    retries: 1,
    rejection_counts: [
      { reason_code: "distractor_quality_unmet", count: 1 },
    ],
    error_count: 0,
    prompt_tokens: 30,
    completion_tokens: 12,
    latency_ms: 120,
    headline: null,
    recommended_action: null,
  },
  events: [
    {
      sequence: 4,
      operation: "multiple_choice_generation",
      phase: "attempt_rejected",
      timestamp: 1.12,
      span_id: null,
      parent_span_id: "generation",
      status: "event",
      attempt: 2,
      stage: "repair",
      reason_code: "distractor_quality_unmet",
      quality_label: null,
      tokens: null,
      latency_ms: null,
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.instances = [];
});

describe("ObservatoryDrawer", () => {
  it("shows a safe live trace summary and resumes after the snapshot cursor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(snapshot)),
    );
    vi.stubGlobal("EventSource", FakeEventSource);

    render(
      <ObservatoryDrawer
        open
        traceId="trace-1"
        onClose={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("dialog", { name: "运行观测" }),
    ).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("选择题生成")).toBeInTheDocument();
    expect(screen.getByText("第 2 次")).toBeInTheDocument();
    expect(screen.getByText("repair")).toBeInTheDocument();
    expect(screen.getByText("distractor_quality_unmet")).toBeInTheDocument();
    expect(screen.getByText("120 ms")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("secret user text");

    await waitFor(() => {
      expect(FakeEventSource.instances).toHaveLength(1);
    });
    expect(String(FakeEventSource.instances[0]?.url)).toContain(
      "/api/v1/observability/traces/trace-1/events?after=4",
    );
  });

  it("can be closed without affecting the running trace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(snapshot)),
    );
    vi.stubGlobal("EventSource", FakeEventSource);
    const close = vi.fn();
    const user = userEvent.setup();

    render(
      <ObservatoryDrawer
        open
        traceId="trace-1"
        onClose={close}
      />,
    );

    await user.click(
      await screen.findByRole("button", { name: "关闭运行观测" }),
    );
    expect(close).toHaveBeenCalledOnce();
  });

  it("dismisses the read-only panel from an outside click or Escape", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(snapshot)));
    vi.stubGlobal("EventSource", FakeEventSource);
    const close = vi.fn();
    const user = userEvent.setup();

    const { rerender } = render(
      <ObservatoryDrawer open traceId="trace-1" onClose={close} />,
    );
    await screen.findByRole("dialog", { name: "运行观测" });

    await user.click(document.body);
    expect(close).toHaveBeenCalledOnce();

    rerender(
      <ObservatoryDrawer open={false} traceId="trace-1" onClose={close} />,
    );
    rerender(
      <ObservatoryDrawer open traceId="trace-1" onClose={close} />,
    );
    await user.keyboard("{Escape}");
    expect(close).toHaveBeenCalledTimes(2);
  });

  it("keeps the close control circular and the icon upright on hover", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(snapshot)));
    vi.stubGlobal("EventSource", FakeEventSource);

    render(<ObservatoryDrawer open traceId="trace-1" onClose={vi.fn()} />);
    const close = await screen.findByRole("button", { name: "关闭运行观测" });
    fireEvent.mouseEnter(close);

    expect(close).toHaveStyle({
      width: "36px",
      height: "36px",
      minHeight: "36px",
      aspectRatio: "1 / 1",
      transform: "none",
    });
  });

  it("shows only the next trace loading and error states when trace identity changes", async () => {
    let rejectNextTrace: ((reason?: unknown) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const request =
        input instanceof Request ? input : new Request(String(input));
      if (request.url.endsWith("/trace-1")) {
        return Response.json(snapshot);
      }
      if (request.url.endsWith("/trace-2")) {
        return await new Promise<Response>((_resolve, reject) => {
          rejectNextTrace = reject;
        });
      }
      throw new Error(`Unexpected request: ${request.method} ${request.url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource);

    const { rerender } = render(
      <ObservatoryDrawer open traceId="trace-1" onClose={vi.fn()} />,
    );
    expect(await screen.findByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();

    rerender(
      <ObservatoryDrawer open traceId="trace-2" onClose={vi.fn()} />,
    );
    expect(screen.getByText("正在读取事件脊柱...")).toBeInTheDocument();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
    expect(screen.queryByText("42")).not.toBeInTheDocument();

    await waitFor(() => expect(rejectNextTrace).toBeDefined());
    rejectNextTrace?.(new Error("trace unavailable"));

    expect(
      await screen.findByRole("alert", { name: "" }),
    ).toHaveTextContent("无法读取运行轨迹");
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
    expect(screen.queryByText("42")).not.toBeInTheDocument();
  });
});
