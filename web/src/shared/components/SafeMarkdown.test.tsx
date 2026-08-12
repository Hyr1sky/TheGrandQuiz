import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown } from "./SafeMarkdown";

describe("SafeMarkdown", () => {
  it("hides document front matter and include directives in the reading projection", () => {
    render(
      <SafeMarkdown
        content={"---\ntitle: Internal title\nkeywords: secret\n---\n<!-- @include: header.md -->\n# Visible\n\n正文"}
        stripDocumentPreamble
      />,
    );

    expect(screen.queryByText(/Internal title/)).not.toBeInTheDocument();
    expect(screen.queryByText(/@include/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Visible" })).toBeInTheDocument();
    expect(screen.getByText("正文")).toBeInTheDocument();
  });

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
