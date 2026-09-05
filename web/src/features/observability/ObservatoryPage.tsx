import { useCallback, useState } from "react";
import { StarMapBackdrop } from "../../shared/components/StarMapBackdrop";
import { ThemeProvider } from "../../app/ThemeProvider";
import { ObservatoryDrawer } from "./ObservatoryDrawer";

function initialTraceId(): string | null {
  return new URLSearchParams(globalThis.location.search).get("trace");
}

export function ObservatoryPage() {
  const [traceId, setTraceId] = useState(initialTraceId);
  const selectTrace = useCallback((nextTraceId: string) => {
    setTraceId(nextTraceId);
    const params = new URLSearchParams({
      view: "observatory",
      trace: nextTraceId,
    });
    globalThis.history.replaceState(null, "", `/?${params.toString()}`);
  }, []);

  return (
    <ThemeProvider>
      <div className="observatory-page-shell">
        <StarMapBackdrop />
        <ObservatoryDrawer
          open
          presentation="page"
          traceId={traceId}
          onClose={() => undefined}
          onSelectTrace={selectTrace}
        />
      </div>
    </ThemeProvider>
  );
}
