import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

interface SafeMarkdownProps {
  content: string;
  className?: string;
  stripLeadingHeading?: boolean;
}

function withoutLeadingHeading(content: string): string {
  return content.replace(/^#{1,6}\s+[^\n]+\n+/, "").trim();
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
}: SafeMarkdownProps) {
  return (
    <div className={className}>
      <Markdown components={safeComponents} remarkPlugins={[remarkGfm]}>
        {stripLeadingHeading ? withoutLeadingHeading(content) : content}
      </Markdown>
    </div>
  );
}
