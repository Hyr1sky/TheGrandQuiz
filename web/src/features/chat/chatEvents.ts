/**
 * SSE stream for chat session events.
 * Follows the same reconnect pattern as runEvents.ts — auto-reconnect on
 * disconnect, close on terminal events.
 */

import type { ChatUiEvent } from "../../shared/api/chat";

const EVENT_TYPES = [
  "chat.turn_started",
  "chat.message_delta",
  "chat.tool_call",
  "chat.tool_result",
  "chat.navigation",
  "chat.turn_ended",
  "chat.turn_cancelled",
  "chat.error",
] as const;

const TERMINAL_EVENTS = new Set<string>([
  "chat.turn_ended",
  "chat.turn_cancelled",
  "chat.error",
]);

type ConnectionState = "connected" | "disconnected";

export function streamChatEvents(
  sessionId: string,
  after: number,
  onEvent: (event: ChatUiEvent) => void,
  onConnectionChange: (state: ConnectionState) => void,
): () => void {
  let closed = false;
  let lastSequence = after;
  let source: EventSource | null = null;
  let reconnectTimer: number | null = null;

  const connect = () => {
    if (closed) {
      return;
    }
    source = new EventSource(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/events?after=${lastSequence}`,
    );
    source.onopen = () => onConnectionChange("connected");
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (rawEvent) => {
        const event = JSON.parse(
          (rawEvent as MessageEvent<string>).data,
        ) as ChatUiEvent;
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
