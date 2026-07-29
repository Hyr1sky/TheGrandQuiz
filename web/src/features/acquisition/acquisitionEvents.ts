import type { AcquisitionUiEvent } from "./api";

const EVENT_TYPES = [
  "acquisition.queued",
  "acquisition.started",
  "acquisition.fetched",
  "acquisition.candidates_ready",
  "acquisition.needs_input",
  "acquisition.succeeded",
  "acquisition.failed",
  "acquisition.cancelled",
] as const;

const QUIET_EVENTS = new Set([
  "acquisition.needs_input",
  "acquisition.succeeded",
  "acquisition.failed",
  "acquisition.cancelled",
]);

export function streamAcquisitionEvents(
  runId: string,
  onEvent: (event: AcquisitionUiEvent) => void,
  onConnectionChange: (connected: boolean) => void,
): () => void {
  let closed = false;
  let lastSequence = 0;
  let source: EventSource | null = null;
  let reconnectTimer: number | null = null;

  const connect = () => {
    if (closed) {
      return;
    }
    source = new EventSource(
      `/api/v1/acquisitions/${encodeURIComponent(runId)}/events?after=${lastSequence}`,
    );
    source.onopen = () => onConnectionChange(true);
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (rawEvent) => {
        const event = JSON.parse(
          (rawEvent as MessageEvent<string>).data,
        ) as AcquisitionUiEvent;
        lastSequence = Math.max(lastSequence, event.sequence);
        onEvent(event);
        if (QUIET_EVENTS.has(type)) {
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
      onConnectionChange(false);
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
