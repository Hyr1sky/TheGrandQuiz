import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StarMapBackdrop } from "./StarMapBackdrop";

describe("StarMapBackdrop", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the decorative field hidden from assistive technology", () => {
    const { container } = render(<StarMapBackdrop />);
    const backdrop = container.querySelector(".star-map-backdrop");

    expect(backdrop).toHaveAttribute("aria-hidden", "true");
    expect(backdrop).toHaveAttribute("data-visual", "observatory");
    expect(container.querySelector(".star-map-backdrop__field")).toBeInTheDocument();
  });

  it("keeps parallax static below the desktop breakpoint", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches:
          query === "(hover: hover) and (pointer: fine)" ||
          query === "(prefers-reduced-motion: no-preference)",
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );

    const { container } = render(<StarMapBackdrop />);

    expect(container.querySelector(".star-map-backdrop")).toHaveAttribute(
      "data-motion",
      "static",
    );
  });
});
