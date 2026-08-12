import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

interface SafeMarkdownProps {
  content: string;
  className?: string;
  stripLeadingHeading?: boolean;
  stripDocumentPreamble?: boolean;
}

function withoutLeadingHeading(content: string): string {
  return content.replace(/^#{1,6}\s+[^\n]+\n+/, "").trim();
}

function withoutDocumentPreamble(content: string): string {
  return content
    .replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "")
    .replace(/^\s*<!--\s*@include:[\s\S]*?-->\s*/i, "")
    .trimStart();
}

const safeComponents: Components = {
  img({ alt, src }) {
    return (
      <span className="safe-markdown__blocked-image" role="note">
        图片已阻止：{alt || "未命名图片"}
        {src ? `（${src}）` : null}
      </span>
    );
  },
};

export function SafeMarkdown({
  content,
  className,
  stripLeadingHeading = false,
  stripDocumentPreamble = false,
}: SafeMarkdownProps) {
  const visibleContent = stripDocumentPreamble
    ? withoutDocumentPreamble(content)
    : content;
  return (
    <div className={className}>
      <Markdown components={safeComponents} remarkPlugins={[remarkGfm]}>
        {stripLeadingHeading ? withoutLeadingHeading(visibleContent) : visibleContent}
      </Markdown>
    </div>
  );
}
