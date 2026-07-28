import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "./SafeMarkdown";

describe("SafeMarkdown", () => {
  it("renders untrusted images as inert placeholders", () => {
    render(
      <SafeMarkdown content="![架构图](https://attacker.invalid/pixel.png)" />,
    );

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("架构图");
    expect(screen.getByRole("note")).toHaveTextContent(
      "https://attacker.invalid/pixel.png",
    );
  });

  it("strips only the leading document heading when requested", () => {
    render(
      <SafeMarkdown
        content={"# Runtime\n\n## 核心结构\n\n正文"}
        stripLeadingHeading
      />,
    );

    expect(
      screen.queryByRole("heading", { level: 1, name: "Runtime" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "核心结构" }),
    ).toBeInTheDocument();
  });
});
