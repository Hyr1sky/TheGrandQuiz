import { useEffect, useRef, useState } from "react";
import {
  getTraceSnapshot,
  type TraceSnapshot,
  type TraceUiEvent,
} from "./api";
import { streamTraceEvents } from "./traceEvents";
import "./observatory-drawer.css";

interface ObservatoryDrawerProps {
  open: boolean;
  traceId: string | null;
  onClose: () => void;
}

const STATUS_LABELS: Record<string, string> = {
  idle: "等待运行",
  running: "运行中",
  waiting_input: "等待输入",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
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

function lastSequence(snapshot: TraceSnapshot): number {
  return snapshot.events.reduce(
    (cursor, event) => Math.max(cursor, event.sequence),
    0,
  );
}

export function ObservatoryDrawer({
  open,
  traceId,
  onClose,
}: ObservatoryDrawerProps) {
  const [snapshot, setSnapshot] = useState<TraceSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<
    "connected" | "disconnected"
  >("connected");
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
          setError("无法读取运行轨迹");
        }
        return null;
      }
    };

    const scheduleRefresh = (_event: TraceUiEvent) => {
      if (refreshTimer.current !== null) {
        clearTimeout(refreshTimer.current);
      }
      refreshTimer.current = setTimeout(() => {
        void refresh();
      }, 80);
    };

    setSnapshot(null);
    setError(null);
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

  if (!open) {
    return null;
  }

  return (
    <aside
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
          ×
        </button>
      </header>

      {traceId === null ? (
        <p className="observatory-drawer__empty">
          正在等待运行会话建立。
        </p>
      ) : error !== null ? (
        <p className="observatory-drawer__error" role="alert">
          {error}
        </p>
      ) : snapshot === null ? (
        <p className="observatory-drawer__empty">正在读取事件脊柱...</p>
      ) : (
        <>
          <section
            className="observatory-status"
            aria-label="运行状态"
          >
            <div>
              <span
                className={`observatory-status__beacon observatory-status__beacon--${snapshot.summary.status}`}
                aria-hidden
              />
              <strong>
                {STATUS_LABELS[snapshot.summary.status] ??
                  snapshot.summary.status}
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
              <strong>{snapshot.summary.event_count}</strong>
            </article>
            <article>
              <span>模型调用</span>
              <strong>{snapshot.summary.model_calls}</strong>
            </article>
            <article>
              <span>工具调用</span>
              <strong>{snapshot.summary.tool_calls}</strong>
            </article>
            <article>
              <span>总 Token</span>
              <strong>{snapshot.summary.total_tokens}</strong>
            </article>
            <article>
              <span>错误 / 恢复</span>
              <strong>
                {snapshot.summary.error_count} /{" "}
                {snapshot.summary.recovery_count}
              </strong>
            </article>
            <article>
              <span>总耗时</span>
              <strong>
                {formatDuration(snapshot.summary.latency_ms)}
              </strong>
            </article>
          </section>

          <section
            className="observatory-timeline"
            aria-label="Span 时间线"
          >
            <div className="observatory-section-title">
              <h3>Span 时间线</h3>
              <span>{snapshot.spans.length}</span>
            </div>
            {snapshot.spans.length === 0 ? (
              <p className="observatory-drawer__empty">
                尚未产生可展示的 span。
              </p>
            ) : (
              <ol>
                {snapshot.spans.map((span) => (
                  <li
                    key={span.span_id}
                    className={`observatory-span observatory-span--${span.status}`}
                  >
                    <span
                      className="observatory-span__rail"
                      aria-hidden
                    />
                    <div className="observatory-span__body">
                      <div>
                        <strong>
                          {span.tool_name ?? span.type}
                        </strong>
                        <span>
                          {formatDuration(span.latency_ms)}
                        </span>
                      </div>
                      <p>
                        #{span.start_sequence}
                        {span.tokens === null
                          ? ""
                          : ` · ${span.tokens} tokens`}
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
