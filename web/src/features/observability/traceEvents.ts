import type { TraceUiEvent } from "./api";

function baseUrl(): string {
  return globalThis.location?.origin ?? "http://localhost";
}

export function streamTraceEvents(
  traceId: string,
  after: number,
  onEvent: (event: TraceUiEvent) => void,
  onConnectionChange?: (
    state: "connected" | "disconnected",
  ) => void,
): () => void {
  const path =
    `/api/v1/observability/traces/${encodeURIComponent(traceId)}` +
    `/events?after=${after}`;
  const source = new EventSource(`${baseUrl()}${path}`);

  source.onopen = () => {
    onConnectionChange?.("connected");
  };
  source.onerror = () => {
    onConnectionChange?.("disconnected");
  };
  source.addEventListener("trace.event", (event) => {
    onEvent(
      JSON.parse((event as MessageEvent<string>).data) as TraceUiEvent,
    );
  });

  return () => {
    source.close();
  };
}
