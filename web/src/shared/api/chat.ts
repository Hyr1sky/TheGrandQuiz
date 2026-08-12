/** Chat session API: create sessions, send messages, and type definitions for SSE events. */

export interface SessionView {
  session_id: string;
  trace_id: string;
}

export interface MessageAccepted {
  turn_id: string;
}

export interface TurnCancelled {
  turn_id: string;
  status: "cancelled";
}

export interface ChatStatusView {
  session_id: string;
  trace_id: string;
  status: "idle" | "running" | "closed";
  context: {
    estimated_tokens: number;
    budget_tokens: number;
    remaining_tokens: number;
    estimation: "heuristic";
  } | null;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
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

export async function getChatStatus(sessionId: string): Promise<ChatStatusView> {
  const response = await globalThis.fetch(
    new Request(
      `${baseUrl()}/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/status`,
    ),
  );
  if (!response.ok) {
    throw new Error("无法读取会话状态");
  }
  return (await response.json()) as ChatStatusView;
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

export async function cancelTurn(
  sessionId: string,
  turnId: string,
): Promise<TurnCancelled> {
  const response = await globalThis.fetch(
    new Request(
      `${baseUrl()}/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/cancel`,
      { method: "POST" },
    ),
  );
  if (!response.ok) {
    throw new Error("无法停止生成");
  }
  return (await response.json()) as TurnCancelled;
}
