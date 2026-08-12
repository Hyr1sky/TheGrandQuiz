import {
  ChartDonutIcon,
  CaretDownIcon,
  CaretUpIcon,
  PaperclipIcon,
  PaperPlaneTiltIcon,
  SidebarSimpleIcon,
  StopIcon,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import {
  cancelTurn,
  createSession,
  getChatStatus,
  sendMessage,
  type ChatStatusView,
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
  activeResourceLabel?: string | null;
  collapsed?: boolean;
  onCollapse?: () => void;
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
  kind?: "assessment-status" | "runtime-status";
  status?: ChatStatusView;
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
  activeResourceLabel = null,
  collapsed = false,
  onCollapse,
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
  const [runtimeStatus, setRuntimeStatus] = useState<ChatStatusView | null>(null);
  const [statusExpanded, setStatusExpanded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const stopStream = useRef<(() => void) | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastSequence = useRef(0);
  const lastSubmittedInput = useRef<string | null>(null);

  const fitTextarea = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea === null) return;
    textarea.style.height = "auto";
    const nextHeight = Math.max(24, Math.min(textarea.scrollHeight, 160));
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > 160 ? "auto" : "hidden";
  }, []);

  useLayoutEffect(() => {
    fitTextarea();
  }, [fitTextarea, input]);

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
          void getChatStatus(view.session_id)
            .then((status) => {
              if (active) setRuntimeStatus(status);
            })
            .catch(() => undefined);
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

  const refreshStatus = useCallback(async (id: string) => {
    const status = await getChatStatus(id);
    setRuntimeStatus(status);
    return status;
  }, []);

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
        if (sessionId !== null) void refreshStatus(sessionId).catch(() => undefined);
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
        if (sessionId !== null) void refreshStatus(sessionId).catch(() => undefined);
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
  }, [refreshStatus, sessionId]);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if (sessionId === null || text === "" || loading) {
      return;
    }
    if (text === "/status") {
      setInput("");
      setStatusExpanded(false);
      setError(null);
      try {
        const status = await refreshStatus(sessionId);
        setMessages((previous) => [
          ...previous,
          { role: "system", content: "", kind: "runtime-status", status },
        ]);
      } catch {
        setError("无法读取会话状态");
      }
      return;
    }
    setError(null);
    lastSubmittedInput.current = text;
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setStatusExpanded(false);
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
      className={`chat-panel${collapsed ? " chat-panel--collapsed" : ""}`}
      aria-label="Agent 对话"
      data-onboarding="chat"
    >
      {collapsed ? (
        <button className="chat-panel__expand" type="button" aria-label="展开对话栏" onClick={onCollapse}>
          <SidebarSimpleIcon aria-hidden size={17} /><strong>对话</strong>
        </button>
      ) : (
      <>
      <header className="chat-panel__header">
        <div>
          <strong>材料对话</strong>
          <span>{connection === "connected" ? "已连接" : "重连中"}</span>
        </div>
        {onCollapse ? (
          <button type="button" aria-label="收起对话栏" onClick={onCollapse}>
            <SidebarSimpleIcon aria-hidden size={17} />
          </button>
        ) : null}
      </header>
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
              {message.kind === "runtime-status" && message.status ? (
                <RuntimeStatusCard status={message.status} />
              ) : message.role === "agent" ? (
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
      <form className="chat-composer" onSubmit={send}>
        <textarea
          ref={textareaRef}
          aria-label="发送消息"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="向材料提问，或输入 /status..."
          disabled={sessionId === null}
        />
        <div className="chat-composer__footer">
          <button
            className="chat-composer__meta"
            type="button"
            aria-label="查看会话状态详情"
            aria-expanded={statusExpanded}
            aria-controls="chat-runtime-status-details"
            onClick={() => setStatusExpanded((value) => !value)}
          >
            <span>
              <PaperclipIcon aria-hidden size={14} />
              {activeResourceLabel ?? "无材料"}
            </span>
            {runtimeStatus?.context ? (
              <span>
                <ChartDonutIcon aria-hidden size={14} />
                {formatCompact(runtimeStatus.context.estimated_tokens)} / {formatCompact(runtimeStatus.context.budget_tokens)}
              </span>
            ) : null}
            {statusExpanded ? <CaretDownIcon aria-hidden size={11} /> : <CaretUpIcon aria-hidden size={11} />}
          </button>
          {loading ? (
            <button
              type="button"
              aria-label="停止生成"
              disabled={activeTurnId === null}
              onClick={() => void cancel()}
            >
              <StopIcon aria-hidden size={17} weight="fill" />
            </button>
          ) : (
            <button
              type="submit"
              aria-label="发送"
              disabled={sessionId === null || input.trim() === ""}
            >
              <PaperPlaneTiltIcon aria-hidden size={17} weight="fill" />
            </button>
          )}
        </div>
        {statusExpanded && runtimeStatus !== null ? (
          <div className="chat-composer__status-popover" id="chat-runtime-status-details">
            <RuntimeStatusCard status={runtimeStatus} />
          </div>
        ) : null}
      </form>
      </>
      )}
    </aside>
  );
}

function formatCompact(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k` : String(value);
}

function RuntimeStatusCard({ status }: { status: ChatStatusView }) {
  return (
    <div className="runtime-status-card" role="status">
      <div><strong>会话状态</strong><span>{status.status === "running" ? "运行中" : "就绪"}</span></div>
      <dl>
        <div><dt>本会话累计</dt><dd>{status.usage.total_tokens.toLocaleString()} tokens</dd></div>
        <div><dt>输入 / 输出</dt><dd>{status.usage.prompt_tokens.toLocaleString()} / {status.usage.completion_tokens.toLocaleString()}</dd></div>
        <div><dt>上下文估算</dt><dd>{status.context ? `${status.context.estimated_tokens.toLocaleString()} / ${status.context.budget_tokens.toLocaleString()}` : "暂不可用"}</dd></div>
        <div><dt>估算余量</dt><dd>{status.context ? status.context.remaining_tokens.toLocaleString() : "—"}</dd></div>
      </dl>
      <p>上下文为本地启发式估算；Token 累计来自模型真实 usage。</p>
    </div>
  );
}
