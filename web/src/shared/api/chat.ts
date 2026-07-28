/** Chat session API: create sessions, send messages, and type definitions for SSE events. */

export interface SessionView {
  session_id: string;
  trace_id: string;
}

export interface MessageAccepted {
  turn_id: string;
}

export interface ChatUiEvent {
  sequence: number;
  type: string;
  session_id: string;
  data: Record<string, unknown>;
}

function baseUrl(): string {
  return globalThis.location?.origin ?? "http://localhost";
}

export async function createSession(): Promise<SessionView> {
  const response = await globalThis.fetch(
    new Request(`${baseUrl()}/api/v1/chat/sessions`, { method: "POST" }),
  );
  if (!response.ok) {
    throw new Error("无法创建对话会话");
  }
  return (await response.json()) as SessionView;
}

export async function sendMessage(
  sessionId: string,
  text: string,
  activeResourceId: string | null = null,
): Promise<MessageAccepted> {
  const response = await globalThis.fetch(
    new Request(
      `${baseUrl()}/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          active_resource_id: activeResourceId,
        }),
      },
    ),
  );
  if (!response.ok) {
    throw new Error("无法发送消息");
  }
  return (await response.json()) as MessageAccepted;
}
