import {
  PaperPlaneTiltIcon,
  StopIcon,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import {
  cancelTurn,
  createSession,
  sendMessage,
  type ChatUiEvent,
} from "../../shared/api/chat";
import { SafeMarkdown } from "../../shared/components/SafeMarkdown";
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
  assessmentStatus?:
    | "preparing"
    | "awaiting_answer"
    | "grading"
    | "judged"
    | "completed"
    | "refused"
    | "failed"
    | "cancelled"
    | null;
}

interface ChatMessage {
  role: "user" | "agent" | "system";
  content: string;
  kind?: "assessment-status";
  turnId?: string;
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

const EXAMPLE_PROMPTS = [
  "请结合当前材料解释核心观点",
  "考我 3 道简答题",
  "怎样查看本次运行过程？",
];

function toolCallLabel(name: string): string {
  return TOOL_LABELS[name] ?? `正在调用 ${name}...`;
}

export function ChatPanel({
  onNavigation,
  onTraceChange,
  activeResourceId = null,
  assessmentStatus = null,
}: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toolCall, setToolCall] = useState<ToolCallInfo | null>(null);
  const [connection, setConnection] = useState<
    "connected" | "disconnected"
  >("connected");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const stopStream = useRef<(() => void) | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastSequence = useRef(0);
  const lastSubmittedInput = useRef<string | null>(null);

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
  useEffect(() => {
    onNavigationRef.current = onNavigation;
  }, [onNavigation]);

  const onChatEvent = useCallback((event: ChatUiEvent) => {
    lastSequence.current = Math.max(lastSequence.current, event.sequence);
    switch (event.type) {
      case "chat.turn_started":
        setLoading(true);
        setToolCall(null);
        if (typeof event.data.turn_id === "string") {
          setActiveTurnId(event.data.turn_id);
        }
        break;
      case "chat.message_delta": {
        const text =
          typeof event.data.text === "string" ? event.data.text : "";
        const turnId =
          typeof event.data.turn_id === "string"
            ? event.data.turn_id
            : "";
        if (text === "" || turnId === "") {
          break;
        }
        setToolCall(null);
        setMessages((previous) => {
          const index = previous.findIndex(
            (message) =>
              message.role === "agent" &&
              message.turnId === turnId,
          );
          if (index === -1) {
            return [
              ...previous,
              { role: "agent", content: text, turnId },
            ];
          }
          return previous.map((message, messageIndex) =>
            messageIndex === index
              ? {
                  ...message,
                  content: `${message.content}${text}`,
                }
              : message,
          );
        });
        break;
      }
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
        if (target === "assessment") {
          setMessages((previous) => [
            ...previous.filter(
              (message) => message.kind !== "assessment-status",
            ),
            {
              role: "system",
              content: label,
              kind: "assessment-status",
            },
          ]);
        } else {
          setMessages((previous) => [
            ...previous,
            { role: "system", content: label },
          ]);
        }
        onNavigationRef.current?.({ target, params });
        break;
      }
      case "chat.turn_ended": {
        const output =
          typeof event.data.output === "string" ? event.data.output : "";
        const turnId =
          typeof event.data.turn_id === "string"
            ? event.data.turn_id
            : "";
        if (output !== "") {
          setMessages((previous) => {
            const index = previous.findIndex(
              (message) =>
                message.role === "agent" &&
                message.turnId === turnId,
            );
            if (index === -1) {
              return [
                ...previous,
                { role: "agent", content: output, turnId },
              ];
            }
            return previous.map((message, messageIndex) =>
              messageIndex === index
                ? { ...message, content: output }
                : message,
            );
          });
        }
        setLoading(false);
        setActiveTurnId(null);
        setToolCall(null);
        break;
      }
      case "chat.turn_cancelled":
        setMessages((previous) => [
          ...previous,
          { role: "system", content: "已停止生成。" },
        ]);
        setLoading(false);
        setActiveTurnId(null);
        setToolCall(null);
        break;
      case "chat.error": {
        const errorType =
          typeof event.data.error === "string"
            ? event.data.error
            : "未知错误";
        setError(`Agent 错误: ${errorType}`);
        setLoading(false);
        setActiveTurnId(null);
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
    lastSubmittedInput.current = text;
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const accepted = await sendMessage(
        sessionId,
        text,
        activeResourceId,
      );
      setActiveTurnId(accepted.turn_id);
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
      setActiveTurnId(null);
    }
  };

  const cancel = async () => {
    if (sessionId === null || activeTurnId === null) {
      return;
    }
    setError(null);
    try {
      await cancelTurn(sessionId, activeTurnId);
    } catch {
      setError("无法停止生成，请稍后重试");
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "ArrowUp" &&
      input === "" &&
      lastSubmittedInput.current !== null
    ) {
      event.preventDefault();
      const previousInput = lastSubmittedInput.current;
      setInput(previousInput);
      queueMicrotask(() => {
        textareaRef.current?.setSelectionRange(
          previousInput.length,
          previousInput.length,
        );
      });
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(event as unknown as FormEvent);
    }
  };

  return (
    <aside
      className="chat-panel"
      aria-label="Agent 对话"
      data-onboarding="chat"
    >
      <div className="chat-messages" role="log" aria-live="polite">
        {messages.length === 0 && !loading ? (
          <div className="chat-empty">
            <p>从一句话开始</p>
            <span>试试这些常用操作：</span>
            <div className="chat-empty__examples">
              {EXAMPLE_PROMPTS.map((prompt) => (
                <button
                  type="button"
                  key={prompt}
                  onClick={() => {
                    setInput(prompt);
                    textareaRef.current?.focus();
                  }}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {messages.map((message, index) => {
          const content =
            message.kind === "assessment-status" &&
            assessmentStatus !== null
              ? assessmentStatus === "completed"
                ? "本轮考核已完成。"
                : assessmentStatus === "refused" ||
                    assessmentStatus === "failed"
                  ? "本轮考核未能开始。"
                  : assessmentStatus === "cancelled"
                    ? "本轮考核已取消。"
                    : assessmentStatus === "preparing"
                      ? "正在为你准备考核..."
                      : "考核进行中，请在工作面板完成本轮题目。"
              : message.content;
          return (
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
                <SafeMarkdown content={content} />
              ) : (
                <div>{content}</div>
              )}
            </div>
          );
        })}
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
        {loading ? (
          <button
            type="button"
            aria-label="停止生成"
            disabled={activeTurnId === null}
            onClick={() => void cancel()}
          >
            <StopIcon aria-hidden size={19} weight="fill" />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="发送"
            disabled={sessionId === null || input.trim() === ""}
          >
            <PaperPlaneTiltIcon aria-hidden size={19} />
          </button>
        )}
      </form>
    </aside>
  );
}
