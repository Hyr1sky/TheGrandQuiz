import {
  useEffect,
  useRef,
  type RefObject,
} from "react";

interface DismissibleLayerOptions {
  open: boolean;
  onDismiss: () => void;
  ignoredRefs?: readonly RefObject<HTMLElement | null>[];
}

export function useDismissibleLayer<T extends HTMLElement>({
  open,
  onDismiss,
  ignoredRefs = [],
}: DismissibleLayerOptions): RefObject<T | null> {
  const layerRef = useRef<T>(null);
  const onDismissRef = useRef(onDismiss);
  const ignoredRefsRef = useRef(ignoredRefs);

  useEffect(() => {
    onDismissRef.current = onDismiss;
    ignoredRefsRef.current = ignoredRefs;
  });

  useEffect(() => {
    if (!open) return;

    const dismissOutside = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      if (layerRef.current?.contains(event.target)) return;
      if (
        ignoredRefsRef.current.some((ref) =>
          ref.current?.contains(event.target as Node),
        )
      ) {
        return;
      }
      onDismissRef.current();
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onDismissRef.current();
      }
    };

    document.addEventListener("pointerdown", dismissOutside);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOutside);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [open]);

  return layerRef;
}
