import { BookOpenTextIcon, ExamIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { AssessmentWorkspace } from "../features/assessment-workspace/AssessmentWorkspace";
import { ArticleWorkspaceRoute } from "../routes/ArticleWorkspaceRoute";
import { ThemeToggle } from "../shared/components/ThemeToggle";
import { ThemeProvider } from "./ThemeProvider";

export function App() {
  const [mode, setMode] = useState<"read" | "assessment">(
    window.location.hash === "#assessment" ? "assessment" : "read",
  );

  const changeMode = (next: "read" | "assessment") => {
    window.location.hash = next === "assessment" ? "assessment" : "";
    setMode(next);
  };

  return (
    <ThemeProvider>
      <div className="app-shell">
        <nav className="workspace-mode-nav" aria-label="工作台模式">
          <button
            type="button"
            aria-pressed={mode === "read"}
            aria-label="进入阅读模式"
            onClick={() => changeMode("read")}
          >
            <BookOpenTextIcon aria-hidden size={18} />
            阅读
          </button>
          <button
            type="button"
            aria-pressed={mode === "assessment"}
            aria-label="进入考核模式"
            onClick={() => changeMode("assessment")}
          >
            <ExamIcon aria-hidden size={18} />
            考核
          </button>
        </nav>
        <ThemeToggle />
        {mode === "read" ? <ArticleWorkspaceRoute /> : <AssessmentWorkspace />}
      </div>
    </ThemeProvider>
  );
}
