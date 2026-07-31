import {
  BookOpenTextIcon,
  CompassIcon,
  ExamIcon,
  FolderPlusIcon,
  ListBulletsIcon,
  QuestionIcon,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ChatPanel, type NavigationEvent } from "../features/chat/ChatPanel";
import { AcquisitionDrawer } from "../features/acquisition/AcquisitionDrawer";
import { ObservatoryDrawer } from "../features/observability/ObservatoryDrawer";
import { OnboardingTour } from "../features/onboarding/OnboardingTour";
import {
  AssessmentPanel,
  type AssessmentPanelHandle,
} from "../features/assessment-workspace/AssessmentPanel";
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
const ONBOARDING_STORAGE_KEY = "grandquiz.onboarding.v1";

interface AssessmentParams {
  resource_id: string;
  question_type_plan: Array<string | null>;
}

export function App() {
  const [resources, setResources] = useState<ResourceSummary[]>([]);
  const [resource, setResource] = useState<ResourceSummary | null>(null);
  const [outline, setOutline] = useState<DocumentNodeSummary[]>([]);
  const [node, setNode] = useState<DocumentNodeRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chatTraceId, setChatTraceId] = useState<string | null>(null);
  const [observatoryOpen, setObservatoryOpen] = useState(false);
  const [acquisitionOpen, setAcquisitionOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(
    () =>
      globalThis.localStorage?.getItem(ONBOARDING_STORAGE_KEY) !==
      "completed",
  );

  const [workspace, setWorkspace] = useState<WorkspaceMode>("reading");
  const [assessmentParams, setAssessmentParams] =
    useState<AssessmentParams | null>(null);
  const [assessmentEpoch, setAssessmentEpoch] = useState(0);
  const assessmentPanelRef = useRef<AssessmentPanelHandle>(null);
  const navigationPendingRef = useRef(false);

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

  const handleAcquisitionCompleted = async (resourceId: string) => {
    try {
      const loaded = await listResources();
      setResources(loaded);
      const imported = loaded.find(
        (candidate) => candidate.resource_id === resourceId,
      );
      if (imported !== undefined) {
        await changeResource(imported);
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "材料已入库，但刷新列表失败",
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
      if (navigationPendingRef.current) {
        return;
      }
      navigationPendingRef.current = true;
      void (async () => {
        try {
          const activePanel = assessmentPanelRef.current;
          if (activePanel !== null && !(await activePanel.cancel())) {
            return;
          }
          if (nav.target === "assessment") {
            const params = nav.params;
            const rawQuestionTypePlan = params.question_type_plan;
            const questionTypePlan = Array.isArray(rawQuestionTypePlan)
              ? rawQuestionTypePlan
                  .filter(
                    (value): value is string | null =>
                      typeof value === "string" || value === null,
                  )
                  .slice(0, 20)
              : [];
            setAssessmentParams({
              resource_id:
                typeof params.resource_id === "string"
                  ? params.resource_id
                  : "",
              question_type_plan:
                questionTypePlan.length > 0
                  ? questionTypePlan
                  : [null, null, null],
            });
            setAssessmentEpoch((current) => current + 1);
            setAssessment(null);
            setRoundHistory([]);
            setWorkspace("assessment");
          } else {
            setWorkspace("reading");
            setAssessmentParams(null);
            setAssessment(null);
            setRoundHistory([]);
          }
        } finally {
          navigationPendingRef.current = false;
        }
      })();
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

  const completeOnboarding = useCallback(() => {
    globalThis.localStorage?.setItem(
      ONBOARDING_STORAGE_KEY,
      "completed",
    );
    setOnboardingOpen(false);
  }, []);

  const renderSidebar = () => {
    return (
      <nav
        className="app-sidebar"
        data-onboarding="sidebar"
        aria-label={sidebarView === "outline" ? "文档大纲" : "考核进度"}
      >
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
            key={assessmentEpoch}
            ref={assessmentPanelRef}
            resourceId={assessmentParams.resource_id}
            questionTypePlan={assessmentParams.question_type_plan}
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
          <div className="empty-library">
            <FolderPlusIcon aria-hidden size={34} weight="duotone" />
            <h1>知识星图还没有材料</h1>
            <p>上传 Markdown、纯文本，或从公开网页开始第一次深读。</p>
            <button
              type="button"
              onClick={() => setAcquisitionOpen(true)}
            >
              添加第一份材料
            </button>
          </div>
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
          <div
            className="app-header__controls"
            data-onboarding="resource"
          >
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
            <button
              type="button"
              className="acquisition-launcher"
              aria-label="添加与管理材料"
              aria-expanded={acquisitionOpen}
              onClick={() => setAcquisitionOpen(true)}
            >
              <FolderPlusIcon aria-hidden size={17} />
              <span>添加材料</span>
            </button>
            <button
              type="button"
              className="onboarding-help"
              aria-label="打开新手指南"
              onClick={() => setOnboardingOpen(true)}
            >
              <QuestionIcon aria-hidden size={17} />
            </button>
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
            data-onboarding="observatory"
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
        <AcquisitionDrawer
          open={acquisitionOpen}
          onClose={() => setAcquisitionOpen(false)}
          onCompleted={(resourceId) => {
            void handleAcquisitionCompleted(resourceId);
          }}
        />
        {onboardingOpen ? (
          <OnboardingTour onComplete={completeOnboarding} />
        ) : null}
      </div>
    </ThemeProvider>
  );
}
