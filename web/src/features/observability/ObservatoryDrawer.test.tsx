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
  summary: {
    trace_id: "trace-1",
    status: "completed",
    event_count: 4,
    model_calls: 1,
    tool_calls: 0,
    error_count: 0,
    recovery_count: 0,
    total_tokens: 42,
    started_at: 1,
    updated_at: 1.12,
    latency_ms: 120,
  },
  spans: [
    {
      span_id: "turn-1",
      parent_span_id: null,
      type: "agent_turn",
      status: "completed",
      start_sequence: 1,
      started_at: 1,
      ended_at: 1.12,
      latency_ms: 120,
      tokens: 42,
      tool_name: null,
    },
  ],
  events: [
    {
      sequence: 4,
      type: "agent.turn.ended",
      timestamp: 1.12,
      span_id: "turn-1",
      parent_span_id: null,
      status: "completed",
      tokens: 42,
      latency_ms: 120,
      tool_name: null,
      recovered: false,
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
    expect(screen.getByText("agent_turn")).toBeInTheDocument();
    expect(screen.getAllByText("120 ms")).toHaveLength(2);
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
});
