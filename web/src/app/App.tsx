import {
  BookOpenTextIcon,
  CompassIcon,
  ExamIcon,
  ListBulletsIcon,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ChatPanel, type NavigationEvent } from "../features/chat/ChatPanel";
import { ObservatoryDrawer } from "../features/observability/ObservatoryDrawer";
import { AssessmentPanel } from "../features/assessment-workspace/AssessmentPanel";
import {
  AssessmentProgress,
  type RoundRecord,
} from "../features/assessment-workspace/AssessmentProgress";
import type { AssessmentView } from "../features/assessment-workspace/api";
import {
  getOutline,
  listResources,
  readNode,
  type DocumentNodeRead,
  type DocumentNodeSummary,
  type ResourceSummary,
} from "../features/article-workspace/api";
import { SafeMarkdown } from "../shared/components/SafeMarkdown";
import { ThemeToggle } from "../shared/components/ThemeToggle";
import { ThemeProvider } from "./ThemeProvider";

type WorkspaceMode = "reading" | "assessment";
type SidebarView = "outline" | "progress";

interface AssessmentParams {
  resource_id: string;
  rounds: number;
  question_type: string | null;
}

export function App() {
  const [resources, setResources] = useState<ResourceSummary[]>([]);
  const [resource, setResource] = useState<ResourceSummary | null>(null);
  const [outline, setOutline] = useState<DocumentNodeSummary[]>([]);
  const [node, setNode] = useState<DocumentNodeRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chatTraceId, setChatTraceId] = useState<string | null>(null);
  const [observatoryOpen, setObservatoryOpen] = useState(false);

  const [workspace, setWorkspace] = useState<WorkspaceMode>("reading");
  const [assessmentParams, setAssessmentParams] =
    useState<AssessmentParams | null>(null);

  // Assessment state lifted for sidebar progress
  const [assessment, setAssessment] = useState<AssessmentView | null>(null);
  const [roundHistory, setRoundHistory] = useState<RoundRecord[]>([]);

  // Sidebar view: auto-follows workspace, but user can override
  const [sidebarView, setSidebarView] = useState<SidebarView>("outline");
  const prevWorkspaceRef = useRef<WorkspaceMode>(workspace);

  // Auto-switch sidebar when workspace changes (unless manually overridden)
  useEffect(() => {
    if (prevWorkspaceRef.current !== workspace) {
      prevWorkspaceRef.current = workspace;
      setSidebarView(workspace === "assessment" ? "progress" : "outline");
    }
  }, [workspace]);

  // Track assessment state changes for history
  const handleAssessmentUpdate = useCallback((view: AssessmentView) => {
    setAssessment(view);
    if (view.judgement !== null && view.judgement !== undefined) {
      setRoundHistory((prev) => {
        const exists = prev.some(
          (r) => r.roundIndex === view.round_index,
        );
        if (exists) {
          return prev.map((r) =>
            r.roundIndex === view.round_index
              ? { ...r, verdict: view.judgement?.verdict ?? null }
              : r,
          );
        }
        return [
          ...prev,
          {
            roundIndex: view.round_index,
            verdict: view.judgement?.verdict ?? null,
          },
        ];
      });
    } else if (view.question !== null && view.question !== undefined) {
      setRoundHistory((prev) => {
        const exists = prev.some(
          (r) => r.roundIndex === view.round_index,
        );
        if (!exists) {
          return [
            ...prev,
            { roundIndex: view.round_index, verdict: null },
          ];
        }
        return prev;
      });
    }
  }, []);

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

  const handleNavigation = useCallback(
    (nav: NavigationEvent) => {
      if (nav.target === "assessment") {
        const params = nav.params;
        setAssessmentParams({
          resource_id:
            typeof params.resource_id === "string" ? params.resource_id : "",
          rounds:
            typeof params.rounds === "number" ? params.rounds : 3,
          question_type:
            typeof params.question_type === "string"
              ? params.question_type
              : null,
        });
        setAssessment(null);
        setRoundHistory([]);
        setWorkspace("assessment");
      } else {
        setWorkspace("reading");
        setAssessmentParams(null);
        setAssessment(null);
        setRoundHistory([]);
      }
    },
    [],
  );

  const handleAssessmentClose = useCallback(() => {
    setWorkspace("reading");
    setAssessmentParams(null);
    setAssessment(null);
    setRoundHistory([]);
  }, []);

  const toggleSidebarView = useCallback(() => {
    setSidebarView((prev) =>
      prev === "outline" ? "progress" : "outline",
    );
  }, []);

  const handleChatTraceChange = useCallback((traceId: string) => {
    setChatTraceId(traceId);
  }, []);

  const renderSidebar = () => {
    return (
      <nav className="app-sidebar" aria-label={sidebarView === "outline" ? "文档大纲" : "考核进度"}>
        <div className="sidebar__header">
          <button
            type="button"
            className="sidebar__toggle"
            aria-label={sidebarView === "outline" ? "切换到考核进度" : "切换到文档大纲"}
            onClick={toggleSidebarView}
          >
            {sidebarView === "outline" ? (
              <>
                <ListBulletsIcon aria-hidden size={16} />
                <span>大纲</span>
              </>
            ) : (
              <>
                <ExamIcon aria-hidden size={16} />
                <span>进度</span>
              </>
            )}
          </button>
        </div>
        {sidebarView === "outline" ? (
          <div className="sidebar__content">
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
          </div>
        ) : (
          <div className="sidebar__content">
            <AssessmentProgress
              assessment={assessment}
              history={roundHistory}
            />
          </div>
        )}
      </nav>
    );
  };

  const renderMainContent = () => {
    if (workspace === "assessment" && assessmentParams !== null) {
      return (
        <main className="app-content" aria-label="考核面板">
          <AssessmentPanel
            resourceId={assessmentParams.resource_id}
            rounds={assessmentParams.rounds}
            questionType={assessmentParams.question_type}
            onClose={handleAssessmentClose}
            onUpdate={handleAssessmentUpdate}
          />
        </main>
      );
    }

    return (
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
                <SafeMarkdown
                  className="reading-markdown"
                  content={node.content}
                  stripLeadingHeading
                />
              )}
            </article>
          </>
        )}
      </main>
    );
  };

  const footerWorkspaceLabel =
    workspace === "assessment" ? "考核" : "阅读";
  const footerWorkspaceIcon =
    workspace === "assessment" ? (
      <ExamIcon aria-hidden size={14} />
    ) : (
      <BookOpenTextIcon aria-hidden size={14} />
    );

  return (
    <ThemeProvider>
      <div className="app-shell">
        <div
          className="star-map-backdrop"
          data-visual="observatory"
          aria-hidden="true"
        />
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

        {renderSidebar()}

        {renderMainContent()}

        <ChatPanel
          activeResourceId={resource?.resource_id ?? null}
          assessmentStatus={assessment?.status ?? null}
          onNavigation={handleNavigation}
          onTraceChange={handleChatTraceChange}
        />

        <footer className="app-footer" aria-label="状态栏">
          <button
            type="button"
            className="compass-nav"
            aria-label={
              observatoryOpen ? "关闭运行观测" : "打开运行观测"
            }
            aria-expanded={observatoryOpen}
            aria-controls="runtime-observatory"
            onClick={() => setObservatoryOpen((value) => !value)}
          >
            <CompassIcon aria-hidden size={16} className="compass-nav__icon" />
            <span className="compass-nav__separator" aria-hidden />
            <span className="compass-nav__state">
              {footerWorkspaceIcon}
              {footerWorkspaceLabel}
            </span>
            <span className="compass-nav__separator" aria-hidden />
            <span className="compass-nav__detail">
              {workspace === "assessment" && assessment !== null
                ? `第 ${assessment.round_index} / ${assessment.rounds} 题`
                : resource !== null
                  ? (resource.topic ?? resource.url)
                  : "就绪"}
            </span>
          </button>
        </footer>

        <ObservatoryDrawer
          open={observatoryOpen}
          traceId={assessment?.trace_id ?? chatTraceId}
          onClose={() => setObservatoryOpen(false)}
        />
      </div>
    </ThemeProvider>
  );
}
