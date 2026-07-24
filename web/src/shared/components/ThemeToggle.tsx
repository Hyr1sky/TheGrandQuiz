import { MoonStarsIcon, SunIcon } from "@phosphor-icons/react";
import { useTheme } from "../../app/ThemeProvider";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const next = theme === "dark" ? "亮色" : "暗色";

  return (
    <button
      className="theme-toggle"
      type="button"
      aria-label={`切换至${next}模式`}
      onClick={toggleTheme}
    >
      {theme === "dark" ? <SunIcon aria-hidden size={18} /> : <MoonStarsIcon aria-hidden size={18} />}
      <span>{next}模式</span>
    </button>
  );
}
