import { XIcon } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { useDismissibleLayer } from "../../shared/hooks/useDismissibleLayer";
import { ActivityIndicator } from "../../shared/components/ActivityIndicator";
import {
  getTraceSnapshot,
  type SafeTraceEvent,
  type SafeTraceRun,
  type SafeTraceSummary,
} from "./api";
import { streamTraceEvents } from "./traceEvents";
import "./observatory-drawer.css";

interface ObservatoryDrawerProps {
  open: boolean;
  traceId: string | null;
  onClose: () => void;
  anchorRef?: RefObject<HTMLElement | null>;
}

const STATUS_LABELS: Record<string, string> = {
  idle: "等待运行",
  running: "运行中",
  waiting_input: "等待输入",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const OPERATION_LABELS: Record<SafeTraceEvent["operation"], string> = {
  assessment_run: "考核运行",
  multiple_choice_generation: "选择题生成",
  distractor_judgement: "干扰项评审",
  grading: "判卷",
  learning_commit: "学习事实提交",
  other: "其他运行事件",
};

function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}

function lastSequence(snapshot: SafeTraceRun): number {
  return snapshot.events.reduce(
    (cursor, event) => Math.max(cursor, event.sequence),
    0,
  );
}

function totalTokens(summary: SafeTraceSummary): number | null {
  if (
    summary.prompt_tokens === null ||
    summary.completion_tokens === null
  ) {
    return null;
  }
  return summary.prompt_tokens + summary.completion_tokens;
}

export function ObservatoryDrawer({
  open,
  traceId,
  onClose,
  anchorRef,
}: ObservatoryDrawerProps) {
  const [snapshot, setSnapshot] = useState<SafeTraceRun | null>(null);
  const [error, setError] = useState<{
    traceId: string;
    message: string;
  } | null>(null);
  const [connection, setConnection] = useState<
    "connected" | "disconnected"
  >("connected");
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const drawerRef = useDismissibleLayer<HTMLElement>({
    open,
    onDismiss: onClose,
    ignoredRefs: anchorRef === undefined ? [] : [anchorRef],
  });

  useEffect(() => {
    if (!open || traceId === null) {
      return;
    }

    let active = true;
    let stopStream: (() => void) | null = null;

    const refresh = async () => {
      try {
        const next = await getTraceSnapshot(traceId);
        if (active) {
          setSnapshot(next);
          setError(null);
        }
        return next;
      } catch {
        if (active) {
          setError({ traceId, message: "无法读取运行轨迹" });
        }
        return null;
      }
    };

    const scheduleRefresh = () => {
      if (refreshTimer.current !== null) {
        clearTimeout(refreshTimer.current);
      }
      refreshTimer.current = setTimeout(() => {
        void refresh();
      }, 80);
    };

    void refresh().then((initial) => {
      if (!active || initial === null) {
        return;
      }
      stopStream = streamTraceEvents(
        traceId,
        lastSequence(initial),
        scheduleRefresh,
        setConnection,
      );
    });

    return () => {
      active = false;
      stopStream?.();
      if (refreshTimer.current !== null) {
        clearTimeout(refreshTimer.current);
        refreshTimer.current = null;
      }
    };
  }, [open, traceId]);

  const currentSnapshot =
    snapshot?.trace_id === traceId ? snapshot : null;
  const currentError =
    error !== null && error.traceId === traceId ? error.message : null;

  if (!open) {
    return null;
  }

  return (
    <aside
      ref={drawerRef}
      id="runtime-observatory"
      className="observatory-drawer"
      role="dialog"
      aria-modal="false"
      aria-label="运行观测"
    >
      <header className="observatory-drawer__header">
        <div>
          <p className="observatory-drawer__eyebrow">TRACE OBSERVATORY</p>
          <h2>运行观测</h2>
        </div>
        <button
          type="button"
          className="observatory-drawer__close"
          aria-label="关闭运行观测"
          onClick={onClose}
        >
          <XIcon aria-hidden size={18} />
        </button>
      </header>

      {traceId === null ? (
        <ActivityIndicator
          className="observatory-drawer__empty"
          label="正在等待运行会话建立。"
          tone="brass"
        />
      ) : currentError !== null ? (
        <p className="observatory-drawer__error" role="alert">
          {currentError}
        </p>
      ) : currentSnapshot === null ? (
        <ActivityIndicator
          className="observatory-drawer__empty"
          label="正在读取事件脊柱..."
          detail="运行过程会从同一条事件流持续更新。"
          tone="brass"
        />
      ) : (
        <>
          <section
            className="observatory-status"
            aria-label="运行状态"
          >
            <div>
              <span
                className={`observatory-status__beacon observatory-status__beacon--${currentSnapshot.status}`}
                aria-hidden
              />
              <strong>
                {STATUS_LABELS[currentSnapshot.status] ??
                  currentSnapshot.status}
              </strong>
            </div>
            <span
              className={`observatory-connection observatory-connection--${connection}`}
            >
              {connection === "connected" ? "LIVE" : "RECONNECTING"}
            </span>
          </section>

          <section
            className="observatory-metrics"
            aria-label="运行指标"
          >
            <article>
              <span>事件</span>
              <strong>{currentSnapshot.events.length}</strong>
            </article>
            <article>
              <span>模型调用</span>
              <strong>{currentSnapshot.summary.model_calls}</strong>
            </article>
            <article>
              <span>重试</span>
              <strong>{currentSnapshot.summary.retries}</strong>
            </article>
            <article>
              <span>总 Token</span>
              <strong>{totalTokens(currentSnapshot.summary) ?? "—"}</strong>
            </article>
            <article>
              <span>错误</span>
              <strong>{currentSnapshot.summary.error_count}</strong>
            </article>
            <article>
              <span>总耗时</span>
              <strong>
                {formatDuration(currentSnapshot.summary.latency_ms)}
              </strong>
            </article>
          </section>

          <section
            className="observatory-timeline"
            aria-label="语义事件"
          >
            <div className="observatory-section-title">
              <h3>语义事件</h3>
              <span>{currentSnapshot.events.length}</span>
            </div>
            {currentSnapshot.events.length === 0 ? (
              <p className="observatory-drawer__empty">
                尚未产生可展示的运行事件。
              </p>
            ) : (
              <ol>
                {currentSnapshot.events.map((event) => (
                  <li
                    key={event.sequence}
                    className={`observatory-span observatory-span--${event.status}`}
                  >
                    <span
                      className="observatory-span__rail"
                      aria-hidden
                    />
                    <div className="observatory-span__body">
                      <div>
                        <strong>
                          {OPERATION_LABELS[event.operation]}
                        </strong>
                        <span>
                          {formatDuration(event.latency_ms)}
                        </span>
                      </div>
                      <p className="observatory-span__meta">
                        <span>#{event.sequence}</span>
                        <span>{event.phase}</span>
                        {event.attempt === null ? null : (
                          <span>第 {event.attempt} 次</span>
                        )}
                        {event.stage === null ? null : (
                          <span>{event.stage}</span>
                        )}
                        {event.reason_code === null ? null : (
                          <span>{event.reason_code}</span>
                        )}
                        {event.tokens === null ? null : (
                          <span>{event.tokens} tokens</span>
                        )}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </aside>
  );
}
