import { useEffect, useState } from "react";
import { ChatPanel } from "../features/chat/ChatPanel";
import {
  getOutline,
  listResources,
  readNode,
  type DocumentNodeRead,
  type DocumentNodeSummary,
  type ResourceSummary,
} from "../features/article-workspace/api";
import { ThemeToggle } from "../shared/components/ThemeToggle";
import { ThemeProvider } from "./ThemeProvider";

export function App() {
  const [resources, setResources] = useState<ResourceSummary[]>([]);
  const [resource, setResource] = useState<ResourceSummary | null>(null);
  const [outline, setOutline] = useState<DocumentNodeSummary[]>([]);
  const [node, setNode] = useState<DocumentNodeRead | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          setError(
            reason instanceof Error ? reason.message : "无法打开本地材料",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const changeResource = async (next: ResourceSummary) => {
    setResource(next);
    setOutline([]);
    setNode(null);
    try {
      setOutline(await getOutline(next.resource_id));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取文档大纲",
      );
    }
  };

  const selectNode = async (nodeId: string) => {
    if (resource === null) {
      return;
    }
    try {
      setNode(await readNode(resource.resource_id, nodeId));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取文档节点",
      );
    }
  };

  const readingBody = (content: string): string =>
    content.replace(/^#{1,6}\s+[^\n]+\n+/, "").trim();

  return (
    <ThemeProvider>
      <div className="app-shell">
        <header className="app-header" aria-label="顶栏">
          <p className="app-header__eyebrow">TheGrandQuiz · 本地模式</p>
          <div className="app-header__controls">
            {resource !== null ? (
              <label className="resource-picker">
                <span>当前材料</span>
                <select
                  aria-label="当前材料"
                  value={resource.resource_id}
                  onChange={(event) => {
                    const next = resources.find(
                      (c) => c.resource_id === event.target.value,
                    );
                    if (next !== undefined) {
                      void changeResource(next);
                    }
                  }}
                >
                  {resources.map((candidate) => (
                    <option
                      key={candidate.resource_id}
                      value={candidate.resource_id}
                    >
                      {candidate.topic ?? candidate.url}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <ThemeToggle />
          </div>
        </header>

        <nav className="app-sidebar" aria-label="文档大纲">
          {outline.map((item, index) => (
            <button
              className="outline__item"
              key={item.node_id}
              type="button"
              aria-current={
                node?.node_id === item.node_id ? "location" : undefined
              }
              onClick={() => void selectNode(item.node_id)}
            >
              <span aria-hidden>
                {String(index + 1).padStart(2, "0")}
              </span>
              {item.title ?? item.section_path}
            </button>
          ))}
        </nav>

        <main className="app-content" aria-label="文章内容">
          {error !== null && resource === null ? (
            <p>{error}</p>
          ) : resource === null ? (
            <p>
              {resources.length === 0
                ? "正在打开本地材料..."
                : "知识库中还没有材料。"}
            </p>
          ) : (
            <>
              {resource.topic ? (
                <h1 className="content-title">{resource.topic}</h1>
              ) : null}
              <article className="reading-article" tabIndex={-1}>
                <p className="section-path">
                  {node?.section_path ?? "选择一个章节"}
                </p>
                {node === null ? (
                  <p>从星图中选择一个章节开始阅读。</p>
                ) : (
                  <p>{readingBody(node.content)}</p>
                )}
              </article>
            </>
          )}
        </main>

        <ChatPanel />

        <footer className="app-footer" aria-label="状态栏">
          <span className="app-footer__status">
            {resource !== null
              ? `材料: ${resource.topic ?? resource.url}`
              : "就绪"}
          </span>
        </footer>
      </div>
    </ThemeProvider>
  );
}
