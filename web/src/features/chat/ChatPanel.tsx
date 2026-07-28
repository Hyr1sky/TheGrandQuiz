import { PaperPlaneTiltIcon } from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  createSession,
  sendMessage,
  type ChatUiEvent,
} from "../../shared/api/chat";
import { streamChatEvents } from "./chatEvents";
import "./chat-panel.css";

export interface NavigationEvent {
  target: string;
  params: Record<string, unknown>;
}

interface ChatPanelProps {
  onNavigation?: (nav: NavigationEvent) => void;
  onTraceChange?: (traceId: string) => void;
  activeResourceId?: string | null;
}

interface ChatMessage {
  role: "user" | "agent" | "system";
  content: string;
}

interface ToolCallInfo {
  name: string;
  label: string;
}

const TOOL_LABELS: Record<string, string> = {
  ingest_resource: "正在收录材料...",
  search_nodes: "正在搜索材料...",
  read_node: "正在阅读节点...",
  assess_once: "正在出题...",
  grade_answer: "正在判卷...",
};

function toolCallLabel(name: string): string {
  return TOOL_LABELS[name] ?? `正在调用 ${name}...`;
}

export function ChatPanel({
  onNavigation,
  onTraceChange,
  activeResourceId = null,
}: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toolCall, setToolCall] = useState<ToolCallInfo | null>(null);
  const [connection, setConnection] = useState<
    "connected" | "disconnected"
  >("connected");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const stopStream = useRef<(() => void) | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastSequence = useRef(0);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    const el = messagesEndRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading, toolCall, error]);

  // Create session on mount
  useEffect(() => {
    let active = true;
    void createSession()
      .then((view) => {
        if (active) {
          lastSequence.current = 0;
          setSessionId(view.session_id);
          onTraceChange?.(view.trace_id);
        }
      })
      .catch(() => {
        if (active) {
          setError("无法创建对话会话，请刷新页面重试");
        }
      });
    return () => {
      active = false;
      stopStream.current?.();
    };
  }, [onTraceChange]);

  // Stable ref to avoid re-creating the SSE callback when onNavigation changes
  const onNavigationRef = useRef(onNavigation);
  onNavigationRef.current = onNavigation;

  const onChatEvent = useCallback((event: ChatUiEvent) => {
    lastSequence.current = Math.max(lastSequence.current, event.sequence);
    switch (event.type) {
      case "chat.turn_started":
        setLoading(true);
        setToolCall(null);
        break;
      case "chat.tool_call": {
        const name =
          typeof event.data.name === "string" ? event.data.name : "";
        setToolCall({ name, label: toolCallLabel(name) });
        break;
      }
      case "chat.tool_result":
        // Tool completed; loading continues until turn ends
        break;
      case "chat.navigation": {
        const target =
          typeof event.data.target === "string" ? event.data.target : "";
        const params =
          typeof event.data.params === "object" &&
          event.data.params !== null
            ? (event.data.params as Record<string, unknown>)
            : {};
        const label =
          target === "assessment"
            ? "正在为你准备考核..."
            : "正在切换到文章阅读...";
        setMessages((prev) => [
          ...prev,
          { role: "system", content: label },
        ]);
        onNavigationRef.current?.({ target, params });
        break;
      }
      case "chat.turn_ended": {
        const output =
          typeof event.data.output === "string" ? event.data.output : "";
        setMessages((prev) => [...prev, { role: "agent", content: output }]);
        setLoading(false);
        setToolCall(null);
        break;
      }
      case "chat.error": {
        const errorType =
          typeof event.data.error === "string"
            ? event.data.error
            : "未知错误";
        setError(`Agent 错误: ${errorType}`);
        setLoading(false);
        setToolCall(null);
        break;
      }
    }
  }, []);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if (sessionId === null || text === "" || loading) {
      return;
    }
    setError(null);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      await sendMessage(sessionId, text, activeResourceId);
      stopStream.current?.();
      stopStream.current = streamChatEvents(
        sessionId,
        lastSequence.current,
        onChatEvent,
        setConnection,
      );
    } catch {
      setError("无法发送消息");
      setLoading(false);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(event as unknown as FormEvent);
    }
  };

  return (
    <aside className="chat-panel" aria-label="Agent 对话">
      <div className="chat-messages" role="log" aria-live="polite">
        {messages.map((message, index) => (
          <div
            key={index}
            className={
              message.role === "user"
                ? "chat-bubble--user"
                : message.role === "system"
                  ? "chat-navigation-hint"
                  : "chat-bubble--agent"
            }
          >
            {message.role === "agent" ? (
              <div>
                <Markdown remarkPlugins={[remarkGfm]}>
                  {message.content}
                </Markdown>
              </div>
            ) : (
              <div>{message.content}</div>
            )}
          </div>
        ))}
        {toolCall !== null ? (
          <div className="chat-tool-call" role="status">
            {toolCall.label}
          </div>
        ) : null}
        {loading && toolCall === null ? (
          <div className="chat-loading" role="status">
            Agent 正在思考...
          </div>
        ) : null}
        {error !== null ? (
          <div className="chat-error" role="alert">
            {error}
          </div>
        ) : null}
        {connection === "disconnected" && loading ? (
          <div className="chat-error" role="status">
            实时连接已中断，正在重新连接...
          </div>
        ) : null}
        <div ref={messagesEndRef} />
      </div>
      <form className="chat-input" onSubmit={send}>
        <textarea
          ref={textareaRef}
          aria-label="发送消息"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息..."
          disabled={sessionId === null}
        />
        <button
          type="submit"
          aria-label="发送"
          disabled={sessionId === null || input.trim() === "" || loading}
        >
          <PaperPlaneTiltIcon aria-hidden size={19} />
        </button>
      </form>
    </aside>
  );
}
