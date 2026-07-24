import type { UiEvent } from "./api";

const EVENT_TYPES = [
  "run.queued",
  "run.started",
  "search.completed",
  "node.read",
  "citation.resolved",
  "answer.completed",
  "run.succeeded",
  "run.failed",
  "run.cancelled",
] as const;

const TERMINAL_EVENTS = new Set(["run.succeeded", "run.failed", "run.cancelled"]);

type ConnectionState = "connected" | "disconnected";

export function streamRunEvents(
  runId: string,
  onEvent: (event: UiEvent) => void,
  onConnectionChange: (state: ConnectionState) => void,
): () => void {
  let closed = false;
  let lastSequence = 0;
  let source: EventSource | null = null;
  let reconnectTimer: number | null = null;

  const connect = () => {
    if (closed) {
      return;
    }
    source = new EventSource(`/api/v1/runs/${encodeURIComponent(runId)}/events?after=${lastSequence}`);
    source.onopen = () => onConnectionChange("connected");
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (rawEvent) => {
        const event = JSON.parse((rawEvent as MessageEvent<string>).data) as UiEvent;
        lastSequence = Math.max(lastSequence, event.sequence);
        onEvent(event);
        if (TERMINAL_EVENTS.has(type)) {
          closed = true;
          source?.close();
        }
      });
    }
    source.onerror = () => {
      if (closed) {
        return;
      }
      source?.close();
      onConnectionChange("disconnected");
      reconnectTimer = window.setTimeout(connect, 750);
    };
  };

  connect();
  return () => {
    closed = true;
    source?.close();
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
    }
  };
}
