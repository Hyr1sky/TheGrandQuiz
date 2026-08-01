import {
  CheckCircleIcon,
  CircleNotchIcon,
  FileTextIcon,
  ShieldCheckIcon,
  UploadSimpleIcon,
  WarningCircleIcon,
  XIcon,
} from "@phosphor-icons/react";
import { type ChangeEvent, useEffect, useRef, useState } from "react";
import {
  createDatasetSnapshot,
  importBlindLabels,
  listDatasetSnapshots,
  listEvalCandidates,
  reviewEvalCandidate,
  syncEvalCandidates,
  type DatasetSnapshot,
  type EvalCandidate,
  type GradingSample,
} from "./api";
import "./eval-drawer.css";

interface EvalDrawerProps {
  open: boolean;
  onClose: () => void;
}

const REVIEW_REQUEST_PREFIX = "grandquiz.eval.review.";

function requestIdFor(candidateId: string, decision: string): string {
  const key = `${REVIEW_REQUEST_PREFIX}${candidateId}.${decision}`;
  const existing = globalThis.localStorage?.getItem(key);
  if (existing !== null && existing !== undefined) return existing;
  const created = globalThis.crypto.randomUUID();
  globalThis.localStorage?.setItem(key, created);
  return created;
}

function sampleTitle(candidate: EvalCandidate): string {
  if (candidate.source_kind === "blind_grading_label") {
    return `盲标样本 · ${"sample_id" in candidate.payload ? candidate.payload.sample_id : candidate.dedupe_key}`;
  }
  return `判决纠正 · ${candidate.dedupe_key.slice(0, 12)}`;
}

function parseSamples(raw: string): GradingSample[] {
  const parsed: unknown = JSON.parse(raw);
  const samples = Array.isArray(parsed)
    ? parsed
    : typeof parsed === "object" &&
        parsed !== null &&
        "samples" in parsed &&
        Array.isArray(parsed.samples)
      ? parsed.samples
      : null;
  if (samples === null || samples.length === 0) {
    throw new Error("文件必须是非空样本数组，或包含 samples 数组");
  }
  return samples as GradingSample[];
}

export function EvalDrawer({ open, onClose }: EvalDrawerProps) {
  const [candidates, setCandidates] = useState<EvalCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null);
  const [snapshots, setSnapshots] = useState<DatasetSnapshot[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    void Promise.all([listEvalCandidates(), listDatasetSnapshots()])
      .then(([items, history]) => {
        if (!active) return;
        setCandidates(items);
        setSnapshots(history);
        setSelected(
          new Set(
            items
              .filter((item) => item.review_status === "approved")
              .map((item) => item.candidate_id),
          ),
        );
        setError(null);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "无法读取 Eval 候选");
        }
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [open]);

  const review = async (
    candidate: EvalCandidate,
    decision: "approved" | "rejected",
  ) => {
    setBusy(true);
    setError(null);
    try {
      const reviewed = await reviewEvalCandidate(
        candidate.candidate_id,
        decision,
        requestIdFor(candidate.candidate_id, decision),
      );
      setCandidates((current) =>
        current.map((item) =>
          item.candidate_id === reviewed.candidate_id ? reviewed : item,
        ),
      );
      if (decision === "approved") {
        setSelected((current) => new Set(current).add(reviewed.candidate_id));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审核提交失败");
    } finally {
      setBusy(false);
    }
  };

  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file === undefined) return;
    setBusy(true);
    setError(null);
    try {
      await importBlindLabels(
        parseSamples(await file.text()),
        globalThis.crypto.randomUUID(),
      );
      setCandidates(await listEvalCandidates());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "盲标样本导入失败");
    } finally {
      event.target.value = "";
      setBusy(false);
    }
  };

  const promote = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await createDatasetSnapshot([...selected]);
      setSnapshot(created);
      setSnapshots((current) => [
        created,
        ...current.filter((item) => item.snapshot_id !== created.snapshot_id),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "快照生成失败");
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  const approved = candidates.filter((item) => item.review_status === "approved");

  return (
    <div className="eval-layer" role="presentation">
      <button
        type="button"
        className="eval-layer__scrim"
        aria-label="关闭 Eval 数据管理"
        onClick={onClose}
      />
      <section className="eval-drawer" role="dialog" aria-modal="true" aria-label="Eval 数据管理">
        <header className="eval-drawer__header">
          <div>
            <p>EVAL DATA PROMOTION</p>
            <h2>把反馈变成可信样本</h2>
          </div>
          <button type="button" aria-label="关闭 Eval 数据管理" onClick={onClose}>
            <XIcon aria-hidden size={18} />
          </button>
        </header>

        <div className="eval-drawer__body">
          <section className="eval-intro">
            <ShieldCheckIcon aria-hidden size={24} weight="duotone" />
            <div>
              <strong>两道门：隐私审核，再生成不可变快照</strong>
              <p>纠正记录只做探索；只有盲于模型输出的人工标签可进入发布门。</p>
            </div>
          </section>

          <div className="eval-toolbar">
            <input
              ref={inputRef}
              type="file"
              accept=".json,application/json"
              onChange={(event) => void importFile(event)}
            />
            <button type="button" disabled={busy} onClick={() => inputRef.current?.click()}>
              <UploadSimpleIcon aria-hidden size={16} />
              导入盲标 JSON
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void syncEvalCandidates()
                  .then(setCandidates)
                  .catch((reason: unknown) =>
                    setError(reason instanceof Error ? reason.message : "纠正同步失败"),
                  )
                  .finally(() => setBusy(false));
              }}
            >
              同步判决纠正
            </button>
            <span>{candidates.length} 个活跃候选</span>
          </div>

          {error !== null ? (
            <p className="eval-error" role="alert">
              <WarningCircleIcon aria-hidden size={17} />
              {error}
            </p>
          ) : null}

          {busy && candidates.length === 0 ? (
            <p className="eval-empty"><CircleNotchIcon className="is-spinning" aria-hidden /> 正在读取...</p>
          ) : candidates.length === 0 ? (
            <p className="eval-empty">还没有待审核反馈。完成判决纠正或导入盲标样本后会出现在这里。</p>
          ) : (
            <div className="eval-candidates">
              {candidates.map((candidate) => (
                <article key={candidate.candidate_id}>
                  <div className="eval-candidate__heading">
                    <div>
                      <strong>{sampleTitle(candidate)}</strong>
                      <span>{candidate.source_kind === "blind_grading_label" ? "盲标" : "纠正"}</span>
                    </div>
                    <small>{candidate.release_gate_eligible ? "可计入发布门" : "仅探索"}</small>
                  </div>
                  <details>
                    <summary><FileTextIcon aria-hidden size={15} /> 查看敏感内容</summary>
                    <pre>{JSON.stringify(candidate.payload, null, 2)}</pre>
                  </details>
                  {candidate.review_status === "pending" ? (
                    <div className="eval-actions">
                      <button type="button" disabled={busy} onClick={() => void review(candidate, "approved")}>隐私检查通过</button>
                      <button type="button" disabled={busy} onClick={() => void review(candidate, "rejected")}>拒绝</button>
                    </div>
                  ) : (
                    <label className="eval-reviewed">
                      <input
                        type="checkbox"
                        disabled={candidate.review_status !== "approved"}
                        checked={selected.has(candidate.candidate_id)}
                        onChange={(event) => {
                          setSelected((current) => {
                            const next = new Set(current);
                            if (event.target.checked) next.add(candidate.candidate_id);
                            else next.delete(candidate.candidate_id);
                            return next;
                          });
                        }}
                      />
                      {candidate.review_status === "approved" ? "纳入下一份快照" : "已拒绝"}
                    </label>
                  )}
                </article>
              ))}
            </div>
          )}

          <section className="eval-promotion">
            <div>
              <strong>生成内容哈希快照</strong>
              <span>{selected.size} / {approved.length} 个已批准候选 · 仅保存在本地，不上传、不提交 Git</span>
            </div>
            <button type="button" disabled={busy || selected.size === 0} onClick={() => void promote()}>
              <CheckCircleIcon aria-hidden size={17} />
              生成快照
            </button>
          </section>

          {snapshot !== null ? (
            <section className="eval-snapshot" aria-label="最新数据集快照">
              <strong>快照已固定</strong>
              <code>{snapshot.content_sha256}</code>
              <p>{snapshot.candidate_count} 条 · 发布门 {snapshot.eligible_blind_count} · 探索 {snapshot.exploratory_count}</p>
            </section>
          ) : null}
          {snapshots.length > 0 ? (
            <section className="eval-snapshot-history" aria-label="数据集快照历史">
              <h3>历史快照</h3>
              {snapshots.map((item) => (
                <button type="button" key={item.snapshot_id} onClick={() => setSnapshot(item)}>
                  <code>{item.content_sha256.slice(0, 16)}</code>
                  <span>{item.candidate_count} 条 · 发布门 {item.eligible_blind_count}</span>
                </button>
              ))}
            </section>
          ) : null}
        </div>
      </section>
    </div>
  );
}
