import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { AcquisitionDrawer } from "./AcquisitionDrawer";

class FakeEventSource {
  static current: FakeEventSource | null = null;
  readonly listeners = new Map<
    string,
    Array<(event: MessageEvent<string>) => void>
  >();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string | URL) {
    FakeEventSource.current = this;
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

  emit(type: string, payload: Record<string, unknown>) {
    const event = new MessageEvent<string>(type, {
      data: JSON.stringify(payload),
    });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

const candidate = {
  item_id: "item-1",
  concept: "事件脊柱",
  summary: "事件统一承载 trace、SSE 与回放。",
  confidence: 0.93,
  evidence: ["事件是系统脊柱"],
};

const baseRun = {
  run_id: "run-1",
  trace_id: "trace-1",
  kind: "upload" as const,
  display_name: "runtime.md",
  status: "queued" as const,
  candidates: [],
  resource_id: null,
  error_code: null,
  error_message: null,
  created_at: 1,
  updated_at: 1,
};

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  FakeEventSource.current = null;
});

it("uploads, reviews exact candidates, and commits only the selected items", async () => {
  let pending = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const request =
      input instanceof Request ? input : new Request(String(input));
    const url = request.url;
    if (
      url.includes("/api/v1/acquisitions?") &&
      request.method === "GET"
    ) {
      return Response.json({ items: [] });
    }
    if (url.endsWith("/api/v1/acquisitions") && request.method === "POST") {
      return Response.json(
        {
          ...baseRun,
          resume_token: "resume-secret",
          token_expires_at: 99,
        },
        { status: 201 },
      );
    }
    if (url.endsWith("/api/v1/acquisitions/run-1") && request.method === "GET") {
      return Response.json({
        ...baseRun,
        status: pending ? "needs_input" : "running",
        candidates: pending ? [candidate] : [],
      });
    }
    if (
      url.endsWith("/api/v1/acquisitions/run-1/approval") &&
      request.method === "POST"
    ) {
      expect(await request.json()).toEqual({
        resume_token: "resume-secret",
        approved_item_ids: ["item-1"],
      });
      return Response.json({
        ...baseRun,
        status: "succeeded",
        candidates: [],
        resource_id: "resource-1",
      });
    }
    throw new Error(`Unexpected request: ${request.method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("EventSource", FakeEventSource);
  const completed = vi.fn();
  const user = userEvent.setup();

  const { container } = render(
    <AcquisitionDrawer
      open
      onClose={() => undefined}
      onCompleted={completed}
    />,
  );
  const fileInput = container.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  const file = new File(["# Runtime\n\n事件是系统脊柱。"], "runtime.md", {
    type: "text/markdown",
  });
  await user.upload(fileInput, file);
  await user.click(screen.getByRole("button", { name: "开始解析" }));

  await waitFor(() => expect(FakeEventSource.current).not.toBeNull());
  pending = true;
  act(() => {
    FakeEventSource.current?.emit("acquisition.needs_input", {
      sequence: 3,
      type: "acquisition.needs_input",
      run_id: "run-1",
      trace_id: "trace-1",
      data: {},
    });
  });

  expect(
    await screen.findByText("事件统一承载 trace、SSE 与回放。"),
  ).toBeInTheDocument();
  expect(screen.getByText("事件是系统脊柱")).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "批准 1 个知识点" }),
  );

  await waitFor(() => expect(completed).toHaveBeenCalledWith("resource-1"));
});
