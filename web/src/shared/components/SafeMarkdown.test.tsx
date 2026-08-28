import { fireEvent, render, screen } from "@testing-library/react";
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

  it("loads an external material image only after explicit user consent", () => {
    const url =
      "https://oss.javaguide.cn/github/javaguide/ai/agent/agent-memory-memory-taxonomy.svg";

    render(
      <SafeMarkdown
        content={`![Agent 记忆分类全景图](${url})`}
        imagePolicy="explicit"
      />,
    );

    expect(screen.queryByRole("img")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "加载图片：Agent 记忆分类全景图" }),
    );

    expect(
      screen.getByRole("img", { name: "Agent 记忆分类全景图" }),
    ).toHaveAttribute("src", url);
    expect(
      screen.getByRole("img", { name: "Agent 记忆分类全景图" }),
    ).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(
      screen.getByRole("img", { name: "Agent 记忆分类全景图" }),
    ).toHaveAttribute("loading", "lazy");
  });

  it("never offers to load a non-http image source", () => {
    render(
      <SafeMarkdown
        content="![本地凭证](file:///Users/example/.env)"
        imagePolicy="explicit"
      />,
    );

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /加载图片/ })).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("图片已阻止");
  });

  it("returns a failed external image to an explicit retry state", () => {
    render(
      <SafeMarkdown
        content="![失效架构图](https://example.com/missing.svg)"
        imagePolicy="explicit"
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "加载图片：失效架构图" }),
    );
    fireEvent.error(screen.getByRole("img", { name: "失效架构图" }));

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "重试加载图片：失效架构图" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("图片加载失败");
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
