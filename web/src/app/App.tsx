import {
  BookOpenTextIcon,
  CompassIcon,
  ExamIcon,
  FolderPlusIcon,
  GearSixIcon,
  ShieldCheckIcon,
  ListBulletsIcon,
  DotsThreeIcon,
  QuestionIcon,
  SidebarSimpleIcon,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { ChatPanel, type NavigationEvent } from "../features/chat/ChatPanel";
import { AcquisitionDrawer } from "../features/acquisition/AcquisitionDrawer";
import { ObservatoryDrawer } from "../features/observability/ObservatoryDrawer";
import { EvalDrawer } from "../features/eval-management/EvalDrawer";
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
  readDocument,
  type DocumentRead,
  type DocumentNodeSummary,
  type ResourceSummary,
} from "../features/article-workspace/api";
import { ContinuousDocument } from "../features/article-workspace/ContinuousDocument";
import { ThemeProvider } from "./ThemeProvider";
import { SettingsDrawer } from "../features/settings/SettingsDrawer";

type WorkspaceMode = "reading" | "assessment";
type SidebarView = "outline" | "progress";
const ONBOARDING_STORAGE_KEY = "grandquiz.onboarding.v1";
const WORKSPACE_LAYOUT_STORAGE_KEY = "grandquiz.workspace-layout.v1";

function initialWorkspaceLayout() {
  const fallback = { outlineWidth: 224, chatWidth: 380, outlineCollapsed: false, chatCollapsed: false };
  const raw = globalThis.localStorage?.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
  if (!raw) return fallback;
  try {
    const saved = JSON.parse(raw) as Partial<typeof fallback>;
    return {
      outlineWidth: typeof saved.outlineWidth === "number" ? saved.outlineWidth : fallback.outlineWidth,
      chatWidth: typeof saved.chatWidth === "number" ? saved.chatWidth : fallback.chatWidth,
      outlineCollapsed: typeof saved.outlineCollapsed === "boolean" ? saved.outlineCollapsed : false,
      chatCollapsed: typeof saved.chatCollapsed === "boolean" ? saved.chatCollapsed : false,
    };
  } catch {
    globalThis.localStorage?.removeItem(WORKSPACE_LAYOUT_STORAGE_KEY);
    return fallback;
  }
}

interface AssessmentParams {
  resource_id: string;
  question_type_plan: Array<string | null>;
}

export function App() {
  const [initialLayout] = useState(initialWorkspaceLayout);
  const [resources, setResources] = useState<ResourceSummary[]>([]);
  const [resource, setResource] = useState<ResourceSummary | null>(null);
  const [outline, setOutline] = useState<DocumentNodeSummary[]>([]);
  const [documentView, setDocumentView] = useState<DocumentRead | null>(null);
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chatTraceId, setChatTraceId] = useState<string | null>(null);
  const [observatoryOpen, setObservatoryOpen] = useState(false);
  const [acquisitionOpen, setAcquisitionOpen] = useState(false);
  const [evalOpen, setEvalOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);
  const [outlineWidth, setOutlineWidth] = useState(initialLayout.outlineWidth);
  const [chatWidth, setChatWidth] = useState(initialLayout.chatWidth);
  const [outlineCollapsed, setOutlineCollapsed] = useState(initialLayout.outlineCollapsed);
  const [chatCollapsed, setChatCollapsed] = useState(initialLayout.chatCollapsed);
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
  const managementMenuRef = useRef<HTMLDivElement>(null);
  const navigationPendingRef = useRef(false);

  useEffect(() => {
    if (!managementOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !managementMenuRef.current?.contains(event.target)
      ) {
        setManagementOpen(false);
      }
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setManagementOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [managementOpen]);

  useEffect(() => {
    globalThis.localStorage?.setItem(WORKSPACE_LAYOUT_STORAGE_KEY, JSON.stringify({
      outlineWidth,
      chatWidth,
      outlineCollapsed,
      chatCollapsed,
    }));
  }, [chatCollapsed, chatWidth, outlineCollapsed, outlineWidth]);

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
        const [nodes, document] = await Promise.all([
          getOutline(first.resource_id),
          readDocument(first.resource_id),
        ]);
        if (active) {
          setOutline(nodes);
          setDocumentView(document);
          setActiveNodeId(nodes[0]?.node_id ?? null);
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
    setDocumentView(null);
    setActiveNodeId(null);
    try {
      const [nodes, document] = await Promise.all([
        getOutline(next.resource_id),
        readDocument(next.resource_id),
      ]);
      setOutline(nodes);
      setDocumentView(document);
      setActiveNodeId(nodes[0]?.node_id ?? null);
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

  const selectNode = (nodeId: string) => {
    setActiveNodeId(nodeId);
    const section = document.getElementById(`document-section-${nodeId}`);
    if (typeof section?.scrollIntoView === "function") {
      section.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  const startResize = (side: "outline" | "chat", event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const initial = side === "outline" ? outlineWidth : chatWidth;
    const move = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      const next = side === "outline" ? initial + delta : initial - delta;
      const clamped = Math.min(side === "outline" ? 360 : 520, Math.max(side === "outline" ? 176 : 300, next));
      if (side === "outline") setOutlineWidth(clamped);
      else setChatWidth(clamped);
    };
    const finish = () => {
      globalThis.removeEventListener("pointermove", move);
      globalThis.removeEventListener("pointerup", finish);
    };
    globalThis.addEventListener("pointermove", move);
    globalThis.addEventListener("pointerup", finish, { once: true });
  };

  const resizeWithKeyboard = (side: "outline" | "chat", event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const delta = event.key === "ArrowRight" ? 16 : -16;
    if (side === "outline") setOutlineWidth((width) => Math.min(360, Math.max(176, width + delta)));
    else setChatWidth((width) => Math.min(520, Math.max(300, width - delta)));
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
        className={`app-sidebar${outlineCollapsed ? " app-sidebar--collapsed" : ""}`}
        data-onboarding="sidebar"
        aria-label={sidebarView === "outline" ? "文档大纲" : "考核进度"}
      >
        <div className="sidebar__header">
          {outlineCollapsed ? null : (
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
          )}
          <button
            type="button"
            className="sidebar__collapse"
            aria-label={outlineCollapsed ? "展开大纲栏" : "收起大纲栏"}
            onClick={() => setOutlineCollapsed((value) => !value)}
          >
            <SidebarSimpleIcon aria-hidden size={17} />
          </button>
        </div>
        {outlineCollapsed ? null : sidebarView === "outline" ? (
          <div className="sidebar__content">
            {outline.map((item, index) => (
              <button
                className="outline__item"
                key={item.node_id}
                type="button"
                aria-current={
                  activeNodeId === item.node_id ? "location" : undefined
                }
                onClick={() => selectNode(item.node_id)}
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
              {documentView === null ? (
                <p>正在展开完整材料...</p>
              ) : (
                <ContinuousDocument
                  activeNodeId={activeNodeId}
                  content={documentView.content}
                  outline={outline}
                  onActiveNodeChange={setActiveNodeId}
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
      <div
        className={`app-shell${outlineCollapsed ? " app-shell--outline-collapsed" : ""}${chatCollapsed ? " app-shell--chat-collapsed" : ""}`}
        style={{
          "--outline-width": `${outlineWidth}px`,
          "--chat-width": `${chatWidth}px`,
        } as CSSProperties}
      >
        <div
          className="star-map-backdrop"
          data-visual="observatory"
          aria-hidden="true"
        />
        <header className="app-header" aria-label="顶栏">
          <p className="app-header__eyebrow">TheGrandQuiz · 本地模式</p>
          <div className="app-header__context" data-onboarding="resource">
            {resource !== null ? (
              <label className="resource-picker">
                <span>当前材料</span>
                <select aria-label="当前材料" value={resource.resource_id} onChange={(event) => {
                  const next = resources.find((candidate) => candidate.resource_id === event.target.value);
                  if (next) void changeResource(next);
                }}>
                  {resources.map((candidate) => <option key={candidate.resource_id} value={candidate.resource_id}>{candidate.topic ?? candidate.url}</option>)}
                </select>
              </label>
            ) : null}
            <button type="button" className="acquisition-launcher" aria-label="添加与管理材料" aria-expanded={acquisitionOpen} onClick={() => setAcquisitionOpen(true)}>
              <FolderPlusIcon aria-hidden size={17} /><span>添加材料</span>
            </button>
          </div>
          <div
            className="app-header__utilities"
            data-onboarding="resource"
          >
            <div className="management-menu" ref={managementMenuRef}>
              <button type="button" className="header-control" data-tooltip="管理" aria-label="打开管理菜单" aria-haspopup="menu" aria-expanded={managementOpen} onClick={() => setManagementOpen((value) => !value)}>
                <DotsThreeIcon aria-hidden size={19} weight="bold" />
              </button>
              {managementOpen ? (
                <div className="management-menu__popover" role="menu">
                  <button type="button" role="menuitem" onClick={() => { setEvalOpen(true); setManagementOpen(false); }}><ShieldCheckIcon aria-hidden size={17} />Eval 数据</button>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              className="header-control"
              data-tooltip="设置"
              aria-label="打开应用设置"
              aria-expanded={settingsOpen}
              onClick={() => setSettingsOpen(true)}
            >
              <GearSixIcon aria-hidden size={17} />
            </button>
            <button
              type="button"
              className="header-control"
              data-tooltip="教程"
              aria-label="打开新手指南"
              onClick={() => setOnboardingOpen(true)}
            >
              <QuestionIcon aria-hidden size={17} />
            </button>
          </div>
        </header>

        {renderSidebar()}

        <div className="workspace-resizer workspace-resizer--outline" role="separator" aria-label="调整大纲栏宽度" aria-orientation="vertical" aria-valuemin={176} aria-valuemax={360} aria-valuenow={outlineWidth} tabIndex={outlineCollapsed ? -1 : 0} onPointerDown={(event) => startResize("outline", event)} onKeyDown={(event) => resizeWithKeyboard("outline", event)} />

        {renderMainContent()}

        <div className="workspace-resizer workspace-resizer--chat" role="separator" aria-label="调整对话栏宽度" aria-orientation="vertical" aria-valuemin={300} aria-valuemax={520} aria-valuenow={chatWidth} tabIndex={chatCollapsed ? -1 : 0} onPointerDown={(event) => startResize("chat", event)} onKeyDown={(event) => resizeWithKeyboard("chat", event)} />

        <ChatPanel
          activeResourceId={resource?.resource_id ?? null}
          activeResourceLabel={resource?.topic ?? resource?.url ?? null}
          assessmentStatus={assessment?.status ?? null}
          collapsed={chatCollapsed}
          onCollapse={() => setChatCollapsed((value) => !value)}
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
        <EvalDrawer open={evalOpen} onClose={() => setEvalOpen(false)} />
        <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
        {onboardingOpen ? (
          <OnboardingTour onComplete={completeOnboarding} />
        ) : null}
      </div>
    </ThemeProvider>
  );
}
