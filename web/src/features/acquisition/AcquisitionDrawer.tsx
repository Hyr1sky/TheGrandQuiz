import {
  ArrowClockwiseIcon,
  CheckCircleIcon,
  CircleNotchIcon,
  FileTextIcon,
  LinkIcon,
  UploadSimpleIcon,
  WarningCircleIcon,
  XIcon,
} from "@phosphor-icons/react";
import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  approveAcquisition,
  cancelAcquisition,
  createUpload,
  createUrl,
  getAcquisition,
  listAcquisitions,
  type AcquisitionCreated,
  type AcquisitionView,
} from "./api";
import { streamAcquisitionEvents } from "./acquisitionEvents";
import "./acquisition-drawer.css";

interface AcquisitionDrawerProps {
  open: boolean;
  onClose: () => void;
  onCompleted: (resourceId: string) => void;
}

type InputMode = "upload" | "url";
const TOKEN_PREFIX = "grandquiz.acquisition.token.";
const ACTIVE_STATUSES = new Set(["queued", "running"]);

const STATUS_LABELS: Record<AcquisitionView["status"], string> = {
  queued: "等待处理",
  running: "正在深读",
  needs_input: "等待审批",
  succeeded: "已入库",
  failed: "处理失败",
  cancelled: "已取消",
};

function tokenFor(runId: string): string | null {
  return globalThis.localStorage?.getItem(`${TOKEN_PREFIX}${runId}`) ?? null;
}

function storeToken(run: AcquisitionCreated) {
  globalThis.localStorage?.setItem(
    `${TOKEN_PREFIX}${run.run_id}`,
    run.resume_token,
  );
}

export function AcquisitionDrawer({
  open,
  onClose,
  onCompleted,
}: AcquisitionDrawerProps) {
  const [mode, setMode] = useState<InputMode>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [run, setRun] = useState<AcquisitionView | null>(null);
  const [recent, setRecent] = useState<AcquisitionView[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refreshRecent = useCallback(async () => {
    try {
      setRecent(await listAcquisitions());
    } catch {
      // 历史记录是辅助管理面，不遮蔽当前操作。
    }
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    let active = true;
    void listAcquisitions()
      .then((items) => {
        if (active) setRecent(items);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [open]);

  useEffect(() => {
    const runId = run?.run_id;
    const status = run?.status;
    if (runId === undefined || status === undefined || !ACTIVE_STATUSES.has(status)) {
      return;
    }
    return streamAcquisitionEvents(
      runId,
      () => {
        void getAcquisition(runId).then((next) => {
          setRun(next);
          if (next.status === "needs_input") {
            setSelectedIds(
              new Set(
                (next.candidates ?? []).map(
                  (candidate) => candidate.item_id,
                ),
              ),
            );
          }
          void refreshRecent();
        });
      },
      setConnected,
    );
  }, [refreshRecent, run?.run_id, run?.status]);

  const selectedCount = selectedIds.size;
  const candidates = run?.candidates ?? [];
  const allSelected =
    run !== null &&
    candidates.length > 0 &&
    selectedCount === candidates.length;
  const progressStep = useMemo(() => {
    if (run === null || run.status === "queued") return 0;
    if (run.status === "running") return 1;
    if (run.status === "needs_input") return 2;
    if (run.status === "succeeded") return 3;
    return 1;
  }, [run]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const created =
        mode === "upload"
          ? await createUpload(
              file?.name ?? "",
              file === null ? "" : await file.text(),
            )
          : await createUrl(url.trim());
      storeToken(created);
      setRun(created);
      setSelectedIds(new Set());
      await refreshRecent();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法开始材料导入",
      );
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (run === null) return;
    const token = tokenFor(run.run_id);
    if (token === null) {
      setError("当前浏览器缺少这次导入的审批凭证，请重新导入材料。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await approveAcquisition(
        run.run_id,
        token,
        [...selectedIds],
      );
      setRun(next);
      globalThis.localStorage?.removeItem(`${TOKEN_PREFIX}${run.run_id}`);
      await refreshRecent();
      if (next.resource_id != null) {
        onCompleted(next.resource_id);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审批提交失败");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (run === null) return;
    const token = tokenFor(run.run_id);
    if (token === null) {
      setError("当前浏览器缺少这次导入的控制凭证。");
      return;
    }
    setBusy(true);
    try {
      setRun(await cancelAcquisition(run.run_id, token));
      globalThis.localStorage?.removeItem(`${TOKEN_PREFIX}${run.run_id}`);
      await refreshRecent();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法取消导入");
    } finally {
      setBusy(false);
    }
  };

  const chooseFile = (next: File | undefined) => {
    if (next === undefined) return;
    setFile(next);
    setError(null);
  };

  const reset = () => {
    setRun(null);
    setFile(null);
    setUrl("");
    setSelectedIds(new Set());
    setError(null);
  };

  if (!open) {
    return null;
  }

  return (
    <div className="acquisition-layer" role="presentation">
      <button
        type="button"
        className="acquisition-layer__scrim"
        aria-label="关闭材料管理"
        onClick={onClose}
      />
      <section
        className="acquisition-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="添加与管理材料"
      >
        <header className="acquisition-drawer__header">
          <div>
            <p>KNOWLEDGE ACQUISITION</p>
            <h2>把材料送进知识星图</h2>
          </div>
          <button
            type="button"
            className="acquisition-icon-button"
            aria-label="关闭材料管理"
            onClick={onClose}
          >
            <XIcon aria-hidden size={18} />
          </button>
        </header>

        <div className="acquisition-drawer__body">
          {run === null ? (
            <>
              <div className="acquisition-tabs" role="tablist" aria-label="导入方式">
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "upload"}
                  onClick={() => setMode("upload")}
                >
                  <UploadSimpleIcon aria-hidden size={17} />
                  上传文件
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "url"}
                  onClick={() => setMode("url")}
                >
                  <LinkIcon aria-hidden size={17} />
                  网页链接
                </button>
              </div>

              {mode === "upload" ? (
                <div
                  className="acquisition-dropzone"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event: DragEvent<HTMLDivElement>) => {
                    event.preventDefault();
                    chooseFile(event.dataTransfer.files[0]);
                  }}
                >
                  <input
                    ref={inputRef}
                    type="file"
                    accept=".md,.markdown,.txt,text/markdown,text/plain"
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      chooseFile(event.target.files?.[0])
                    }
                  />
                  <FileTextIcon aria-hidden size={34} weight="duotone" />
                  <strong>{file?.name ?? "拖入 Markdown 或纯文本"}</strong>
                  <span>
                    {file === null
                      ? "正文会先深读、生成候选知识点，审批后才正式入库。"
                      : `${Math.max(1, Math.round(file.size / 1024))} KiB · 等待上传`}
                  </span>
                  <button
                    type="button"
                    onClick={() => inputRef.current?.click()}
                  >
                    选择文件
                  </button>
                </div>
              ) : (
                <label className="acquisition-url-field">
                  <span>公开网页 URL</span>
                  <input
                    type="url"
                    placeholder="https://example.com/article"
                    value={url}
                    onChange={(event) => setUrl(event.target.value)}
                  />
                  <small>
                    服务会执行 SSRF 防护、大小限制和正文质量检查。
                  </small>
                </label>
              )}

              {error !== null ? (
                <p className="acquisition-error" role="alert">
                  <WarningCircleIcon aria-hidden size={17} />
                  {error}
                </p>
              ) : null}

              <button
                type="button"
                className="acquisition-primary"
                disabled={
                  busy ||
                  (mode === "upload" ? file === null : url.trim() === "")
                }
                onClick={() => void start()}
              >
                {busy ? (
                  <CircleNotchIcon className="is-spinning" aria-hidden size={18} />
                ) : (
                  <UploadSimpleIcon aria-hidden size={18} />
                )}
                开始解析
              </button>

              {recent.length > 0 ? (
                <section className="acquisition-history" aria-label="最近导入">
                  <div className="acquisition-section-title">
                    <h3>最近导入</h3>
                    <span>{recent.length} 条</span>
                  </div>
                  {recent.map((item) => (
                    <button
                      type="button"
                      key={item.run_id}
                      onClick={() => {
                        setRun(item);
                        setSelectedIds(
                          new Set(
                            (item.candidates ?? []).map(
                              (candidate) => candidate.item_id,
                            ),
                          ),
                        );
                      }}
                    >
                      <span className={`status-dot status-dot--${item.status}`} />
                      <span>
                        <strong>{item.display_name}</strong>
                        <small>{STATUS_LABELS[item.status]}</small>
                      </span>
                    </button>
                  ))}
                </section>
              ) : null}
            </>
          ) : (
            <>
              <div className="acquisition-run-title">
                <div>
                  {run.kind === "upload" ? (
                    <FileTextIcon aria-hidden size={22} />
                  ) : (
                    <LinkIcon aria-hidden size={22} />
                  )}
                  <span>
                    <strong>{run.display_name}</strong>
                    <small>
                      Trace {run.trace_id.slice(0, 10)}
                      {!connected && ACTIVE_STATUSES.has(run.status)
                        ? " · 正在重连"
                        : ""}
                    </small>
                  </span>
                </div>
                <span className={`acquisition-status acquisition-status--${run.status}`}>
                  {STATUS_LABELS[run.status]}
                </span>
              </div>

              <ol className="acquisition-progress" aria-label="导入进度">
                {["接收材料", "安全深读", "人工审批", "写入星图"].map(
                  (label, index) => (
                    <li
                      key={label}
                      data-state={
                        index < progressStep
                          ? "done"
                          : index === progressStep
                            ? "active"
                            : "idle"
                      }
                    >
                      <span>
                        {index < progressStep ? (
                          <CheckCircleIcon aria-hidden weight="fill" size={18} />
                        ) : (
                          index + 1
                        )}
                      </span>
                      {label}
                    </li>
                  ),
                )}
              </ol>

              {ACTIVE_STATUSES.has(run.status) ? (
                <div className="acquisition-processing">
                  <CircleNotchIcon className="is-spinning" aria-hidden size={28} />
                  <div>
                    <strong>Reader 正在整理材料结构</strong>
                    <p>
                      抽取候选知识点与精确证据。此时不会污染正式知识库。
                    </p>
                  </div>
                </div>
              ) : null}

              {run.status === "needs_input" ? (
                <section className="acquisition-approval">
                  <div className="acquisition-section-title">
                    <div>
                      <h3>确认候选知识点</h3>
                      <p>只勾选值得进入后续考核循环的内容。</p>
                    </div>
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedIds(
                          allSelected
                            ? new Set()
                            : new Set(
                                candidates.map(
                                  (candidate) => candidate.item_id,
                                ),
                              ),
                        )
                      }
                    >
                      {allSelected ? "全部取消" : "全部选择"}
                    </button>
                  </div>
                  <div className="acquisition-candidates">
                    {candidates.map((candidate) => {
                      const selected = selectedIds.has(candidate.item_id);
                      return (
                        <label
                          key={candidate.item_id}
                          className="acquisition-candidate"
                          data-selected={selected}
                        >
                          <input
                            type="checkbox"
                            checked={selected}
                            onChange={() => {
                              const next = new Set(selectedIds);
                              if (selected) next.delete(candidate.item_id);
                              else next.add(candidate.item_id);
                              setSelectedIds(next);
                            }}
                          />
                          <span className="acquisition-candidate__check">
                            {selected ? "✓" : ""}
                          </span>
                          <span className="acquisition-candidate__content">
                            <strong>{candidate.concept}</strong>
                            <p>{candidate.summary}</p>
                            {candidate.evidence.map((evidence) => (
                              <q key={evidence}>{evidence}</q>
                            ))}
                          </span>
                          <span className="acquisition-confidence">
                            {Math.round(candidate.confidence * 100)}%
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </section>
              ) : null}

              {run.status === "succeeded" ? (
                <div className="acquisition-result acquisition-result--success">
                  <CheckCircleIcon aria-hidden size={32} weight="duotone" />
                  <div>
                    <strong>材料已经进入知识星图</strong>
                    <p>文章、大纲与知识点已切换到新的不可变修订。</p>
                  </div>
                </div>
              ) : null}

              {run.status === "failed" || run.status === "cancelled" ? (
                <div className="acquisition-result acquisition-result--error">
                  <WarningCircleIcon aria-hidden size={30} />
                  <div>
                    <strong>
                      {run.status === "failed" ? "这次处理没有完成" : "导入已取消"}
                    </strong>
                    <p>
                      {run.error_message ??
                        "正式知识库没有写入任何半成品，可以安全重试。"}
                    </p>
                  </div>
                </div>
              ) : null}

              {error !== null ? (
                <p className="acquisition-error" role="alert">
                  <WarningCircleIcon aria-hidden size={17} />
                  {error}
                </p>
              ) : null}

              <footer className="acquisition-actions">
                {run.status === "needs_input" ? (
                  <>
                    <button
                      type="button"
                      className="acquisition-secondary"
                      disabled={busy}
                      onClick={() => void cancel()}
                    >
                      取消导入
                    </button>
                    <button
                      type="button"
                      className="acquisition-primary"
                      disabled={busy}
                      onClick={() => void approve()}
                    >
                      {busy ? (
                        <CircleNotchIcon
                          className="is-spinning"
                          aria-hidden
                          size={18}
                        />
                      ) : (
                        <CheckCircleIcon aria-hidden size={18} />
                      )}
                      批准 {selectedCount} 个知识点
                    </button>
                  </>
                ) : ACTIVE_STATUSES.has(run.status) ? (
                  <button
                    type="button"
                    className="acquisition-secondary"
                    disabled={busy}
                    onClick={() => void cancel()}
                  >
                    取消处理
                  </button>
                ) : (
                  <button
                    type="button"
                    className="acquisition-primary"
                    onClick={reset}
                  >
                    <ArrowClockwiseIcon aria-hidden size={18} />
                    再导入一份
                  </button>
                )}
              </footer>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
