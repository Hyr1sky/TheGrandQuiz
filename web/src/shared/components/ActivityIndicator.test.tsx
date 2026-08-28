import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActivityIndicator } from "./ActivityIndicator";

describe("ActivityIndicator", () => {
  it("announces the current stage and preserves supporting detail", () => {
    render(
      <ActivityIndicator
        label="正在判卷"
        detail="正在核对回答与原文证据"
        variant="block"
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("正在判卷");
    expect(status).toHaveTextContent("正在核对回答与原文证据");
  });
});
