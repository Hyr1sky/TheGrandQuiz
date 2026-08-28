import { useEffect, useRef } from "react";

const MAX_PARALLAX_PX = 4;

export function StarMapBackdrop() {
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const backdrop = backdropRef.current;
    if (backdrop === null || typeof globalThis.matchMedia !== "function") {
      return;
    }

    const finePointer = globalThis.matchMedia("(hover: hover) and (pointer: fine)");
    const desktopViewport = globalThis.matchMedia("(min-width: 760px)");
    const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)");
    let frame: number | null = null;

    const resetParallax = () => {
      backdrop.style.setProperty("--star-map-x", "0px");
      backdrop.style.setProperty("--star-map-y", "0px");
    };

    const updateMotionMode = () => {
      backdrop.dataset.motion =
        desktopViewport.matches && finePointer.matches && !reducedMotion.matches
          ? "interactive"
          : "static";
      if (backdrop.dataset.motion === "static") {
        resetParallax();
      }
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (backdrop.dataset.motion !== "interactive" || frame !== null) {
        return;
      }
      const x = (event.clientX / Math.max(globalThis.innerWidth, 1) - 0.5) * 2;
      const y = (event.clientY / Math.max(globalThis.innerHeight, 1) - 0.5) * 2;
      frame = globalThis.requestAnimationFrame(() => {
        backdrop.style.setProperty("--star-map-x", `${(-x * MAX_PARALLAX_PX).toFixed(2)}px`);
        backdrop.style.setProperty("--star-map-y", `${(-y * MAX_PARALLAX_PX).toFixed(2)}px`);
        frame = null;
      });
    };

    const handleVisibility = () => {
      backdrop.dataset.paused = document.hidden ? "true" : "false";
    };

    updateMotionMode();
    handleVisibility();
    finePointer.addEventListener("change", updateMotionMode);
    desktopViewport.addEventListener("change", updateMotionMode);
    reducedMotion.addEventListener("change", updateMotionMode);
    globalThis.addEventListener("pointermove", handlePointerMove, { passive: true });
    globalThis.addEventListener("pointerleave", resetParallax);
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      finePointer.removeEventListener("change", updateMotionMode);
      desktopViewport.removeEventListener("change", updateMotionMode);
      reducedMotion.removeEventListener("change", updateMotionMode);
      globalThis.removeEventListener("pointermove", handlePointerMove);
      globalThis.removeEventListener("pointerleave", resetParallax);
      document.removeEventListener("visibilitychange", handleVisibility);
      if (frame !== null) {
        globalThis.cancelAnimationFrame(frame);
      }
    };
  }, []);

  return (
    <div
      ref={backdropRef}
      className="star-map-backdrop"
      data-visual="observatory"
      data-motion="static"
      data-paused="false"
      aria-hidden="true"
    >
      <div className="star-map-backdrop__drift">
        <div className="star-map-backdrop__field" />
      </div>
    </div>
  );
}
