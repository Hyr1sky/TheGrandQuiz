import {
  BookOpenTextIcon,
  CheckCircleIcon,
  EyeIcon,
  EyeSlashIcon,
  LinkSimpleIcon,
  MagnifyingGlassIcon,
  PaperPlaneTiltIcon,
  XCircleIcon,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  DocumentNodeRead,
  DocumentNodeSummary,
  ResourceSummary,
  RunView,
  UiEvent,
} from "./api";
import {
  cancelRun,
  getOutline,
  getRun,
  listResources,
  readNode,
  startQuestion,
} from "./api";
import { streamRunEvents } from "./runEvents";
import "./article-workspace.css";

const STAGES = [
  { key: "search.completed", label: "搜索材料", icon: MagnifyingGlassIcon },
  { key: "node.read", label: "阅读节点", icon: BookOpenTextIcon },
  { key: "citation.resolved", label: "引用解析", icon: LinkSimpleIcon },
  { key: "run.succeeded", label: "完成", icon: CheckCircleIcon },
] as const;

function readingBody(content: string): string {
  return content.replace(/^#{1,6}\s+[^\n]+\n+/, "").trim();
}

export function ArticleWorkspace() {
  const [resources, setResources] = useState<ResourceSummary[]>([]);
  const [resource, setResource] = useState<ResourceSummary | null>(null);
  const [outline, setOutline] = useState<DocumentNodeSummary[]>([]);
  const [node, setNode] = useState<DocumentNodeRead | null>(null);
  const [question, setQuestion] = useState("");
  const [run, setRun] = useState<RunView | null>(null);
  const [receivedEvents, setReceivedEvents] = useState<Set<string>>(new Set());
  const [connection, setConnection] = useState<"connected" | "disconnected">("connected");
  const [evidenceRevealed, setEvidenceRevealed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopStream = useRef<(() => void) | null>(null);
  const articleRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let active = true;
    void listResources()
      .then(async (loadedResources) => {
        const first = loadedResources[0];
        if (!active) {
          return;
        }
        setResources(loadedResources);
        if (first === undefined) {
          return;
        }
        setResource(first);
        const nodes = await getOutline(first.resource_id);
        if (active) {
          setOutline(nodes);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "无法打开本地材料");
        }
      });
    return () => {
      active = false;
      stopStream.current?.();
    };
  }, []);

  const changeResource = async (next: ResourceSummary) => {
    stopStream.current?.();
    setResource(next);
    setOutline([]);
    setNode(null);
    setRun(null);
    setQuestion("");
    setEvidenceRevealed(false);
    setReceivedEvents(new Set());
    try {
      setOutline(await getOutline(next.resource_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取文档大纲");
    }
  };

  const selectNode = async (nodeId: string) => {
    if (resource === null) {
      return;
    }
    try {
      const selected = await readNode(resource.resource_id, nodeId);
      setNode(selected);
      window.requestAnimationFrame(() => articleRef.current?.focus());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取文档节点");
    }
  };

  const finishRun = async (runId: string) => {
    try {
      setRun(await getRun(runId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取运行结果");
    }
  };

  const onRunEvent = (event: UiEvent) => {
    setReceivedEvents((current) => new Set(current).add(event.type));
    if (event.type === "run.succeeded" || event.type === "run.failed" || event.type === "run.cancelled") {
      void finishRun(event.run_id);
    }
  };

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    if (resource === null || question.trim() === "") {
      return;
    }
    setError(null);
    setEvidenceRevealed(false);
    setReceivedEvents(new Set(["run.queued"]));
    stopStream.current?.();
    try {
      const started = await startQuestion(resource.resource_id, question.trim());
      setRun(started);
      stopStream.current = streamRunEvents(started.run_id, onRunEvent, setConnection);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法开始材料问答");
    }
  };

  const cancel = async () => {
    if (run === null) {
      return;
    }
    try {
      setRun(await cancelRun(run.run_id));
      stopStream.current?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法取消运行");
    }
  };

  if (error !== null && resource === null) {
    return <main aria-label="TheGrandQuiz 学习工作台">{error}</main>;
  }
  if (resource === null) {
    return (
      <main aria-label="TheGrandQuiz 学习工作台">
        {resources.length === 0 ? "正在打开本地材料…" : "知识库中还没有材料。"}
      </main>
    );
  }

  const result = run?.result;
  const citation = result?.citations[0];
  const running = run?.status === "queued" || run?.status === "running";

  return (
    <main className="workspace" aria-label="TheGrandQuiz 学习工作台">
      <header className="workspace__header">
        <p className="workspace__eyebrow">TheGrandQuiz · 本地模式</p>
        <h1>{resource.topic ?? resource.url}</h1>
        <label className="resource-picker">
          <span>当前材料</span>
          <select
            aria-label="当前材料"
            value={resource.resource_id}
            onChange={(event) => {
              const next = resources.find(
                (candidate) => candidate.resource_id === event.target.value,
              );
              if (next !== undefined) {
                void changeResource(next);
              }
            }}
          >
            {resources.map((candidate) => (
              <option key={candidate.resource_id} value={candidate.resource_id}>
                {candidate.topic ?? candidate.url}
              </option>
            ))}
          </select>
        </label>
      </header>

      <nav className="outline" aria-label="文档大纲">
        {outline.map((item, index) => (
          <button
            className="outline__item"
            key={item.node_id}
            type="button"
            aria-current={node?.node_id === item.node_id ? "location" : undefined}
            onClick={() => void selectNode(item.node_id)}
          >
            <span aria-hidden>{String(index + 1).padStart(2, "0")}</span>
            {item.title ?? item.section_path}
          </button>
        ))}
      </nav>

      <section className="reading-surface" aria-label="文章内容">
        <article ref={articleRef} tabIndex={-1}>
          <p className="section-path">{node?.section_path ?? "选择一个章节"}</p>
          {node === null ? (
            <p>从星图中选择一个章节开始阅读。</p>
          ) : (
            <div className="reading-markdown">
              <Markdown remarkPlugins={[remarkGfm]}>
                {readingBody(node.content)}
              </Markdown>
            </div>
          )}
        </article>
      </section>

      <aside className="annotation" aria-label="材料批注">
        <form onSubmit={ask}>
          <label htmlFor="material-question">我的提问</label>
          <textarea
            id="material-question"
            aria-label="针对当前材料的问题"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="沿着材料继续追问…"
          />
          <button type="submit" disabled={running || question.trim() === ""}>
            <PaperPlaneTiltIcon aria-hidden size={19} />
            向材料提问
          </button>
        </form>

        {connection === "disconnected" && running ? (
          <p role="status">实时连接已中断，正在从上一个事件继续…</p>
        ) : null}
        {error !== null ? <p role="alert">{error}</p> : null}
        {run?.error ? (
          <div className="run-error" role="alert">
            <p>{run.error.message}</p>
            {run.error.trace_id ? <code>trace_id: {run.error.trace_id}</code> : null}
          </div>
        ) : null}
        {run?.status === "cancelled" ? (
          <p role="status">运行已取消 · trace_id: {run.trace_id}</p>
        ) : null}

        {result?.status === "no_evidence" ? (
          <section className="answer" aria-label="回答">
            <p>材料中没有足够证据，已停止生成答案。</p>
          </section>
        ) : null}
        {result?.status === "answered" && result.answer ? (
          <section className="answer" aria-label="回答">
            <p className="answer__label">回答（基于本文）</p>
            <p>{result.answer}</p>
            {citation ? (
              <>
                <button
                  className="citation-link"
                  type="button"
                  onClick={() => void selectNode(citation.node_id)}
                >
                  <LinkSimpleIcon aria-hidden size={16} />
                  证据来源 · {citation.section_path}
                </button>
                <section className="evidence" aria-label="引用证据">
                  <p>证据摘录</p>
                  <button
                    type="button"
                    aria-expanded={evidenceRevealed}
                    onClick={() => setEvidenceRevealed((current) => !current)}
                  >
                    {evidenceRevealed ? (
                      <EyeSlashIcon aria-hidden size={18} />
                    ) : (
                      <EyeIcon aria-hidden size={18} />
                    )}
                    {evidenceRevealed ? "隐藏证据" : "揭示证据"}
                  </button>
                  {evidenceRevealed ? (
                    <blockquote>
                      <mark>{citation.quote}</mark>
                      <span>{citation.context.replace(citation.quote, "")}</span>
                    </blockquote>
                  ) : (
                    <div className="evidence__veil" aria-hidden>
                      精确原文已遮罩
                    </div>
                  )}
                </section>
              </>
            ) : null}
          </section>
        ) : null}
      </aside>

      <footer className="run-trail" aria-label="Agentic Search 运行轨迹">
        <p>Agentic Search</p>
        <ol>
          {STAGES.map((stage, index) => {
            const previous = STAGES[index - 1];
            const isCurrent =
              running &&
              !receivedEvents.has(stage.key) &&
              (index === 0 || (previous !== undefined && receivedEvents.has(previous.key)));
            const StageIcon = stage.icon;
            return (
              <li
                key={stage.key}
                data-complete={receivedEvents.has(stage.key)}
                aria-current={isCurrent ? "step" : undefined}
              >
                <span aria-hidden>
                  <StageIcon size={18} />
                </span>
                {stage.label}
              </li>
            );
          })}
        </ol>
        {running ? (
          <button type="button" onClick={() => void cancel()}>
            <XCircleIcon aria-hidden size={18} />
            取消运行
          </button>
        ) : null}
      </footer>
    </main>
  );
}
