import { ArticleWorkspaceRoute } from "../routes/ArticleWorkspaceRoute";
import { ThemeToggle } from "../shared/components/ThemeToggle";
import { ThemeProvider } from "./ThemeProvider";

export function App() {
  return (
    <ThemeProvider>
      <div className="app-shell">
        <ThemeToggle />
        <ArticleWorkspaceRoute />
      </div>
    </ThemeProvider>
  );
}
