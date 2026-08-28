import { useState } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

type ImagePolicy = "blocked" | "explicit";

interface SafeMarkdownProps {
  content: string;
  className?: string;
  stripLeadingHeading?: boolean;
  stripDocumentPreamble?: boolean;
  imagePolicy?: ImagePolicy;
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

interface MarkdownImageProps {
  alt?: string;
  src?: string;
}

function BlockedMarkdownImage({ alt, src }: MarkdownImageProps) {
  return (
    <span className="safe-markdown__blocked-image" role="note">
      图片已阻止：{alt || "未命名图片"}
      {src ? `（${src}）` : null}
    </span>
  );
}

function externalImageUrl(src: string | undefined): string | null {
  if (src === undefined) return null;
  try {
    const url = new URL(src);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function ExplicitMarkdownImage({ alt, src }: MarkdownImageProps) {
  const safeUrl = externalImageUrl(src);
  const label = alt || "未命名图片";
  const [authorizedUrl, setAuthorizedUrl] = useState<string | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  if (safeUrl === null) {
    return <BlockedMarkdownImage alt={alt} src={src} />;
  }

  if (authorizedUrl !== safeUrl) {
    const failed = failedUrl === safeUrl;
    return (
      <span
        className="safe-markdown__blocked-image safe-markdown__blocked-image--actionable"
        role="note"
      >
        <span>
          {failed ? "图片加载失败" : "远程图片默认未加载"}：{label}
        </span>
        <code title={safeUrl}>{safeUrl}</code>
        <button
          type="button"
          aria-label={`${failed ? "重试加载图片" : "加载图片"}：${label}`}
          onClick={() => {
            setFailedUrl(null);
            setAuthorizedUrl(safeUrl);
          }}
        >
          {failed ? "重试" : "加载此图片"}
        </button>
        <a
          href={safeUrl}
          target="_blank"
          rel="noopener noreferrer"
          referrerPolicy="no-referrer"
        >
          打开原图
        </a>
      </span>
    );
  }

  return (
    <span className="safe-markdown__image">
      <img
        alt={label}
        src={safeUrl}
        loading="lazy"
        decoding="async"
        referrerPolicy="no-referrer"
        onError={() => {
          setFailedUrl(safeUrl);
          setAuthorizedUrl(null);
        }}
      />
      <span>{label}</span>
    </span>
  );
}

const blockedImageComponents: Components = {
  img: BlockedMarkdownImage,
};

const explicitImageComponents: Components = {
  img: ExplicitMarkdownImage,
};

export function SafeMarkdown({
  content,
  className,
  stripLeadingHeading = false,
  stripDocumentPreamble = false,
  imagePolicy = "blocked",
}: SafeMarkdownProps) {
  const visibleContent = stripDocumentPreamble
    ? withoutDocumentPreamble(content)
    : content;
  const components =
    imagePolicy === "explicit" ? explicitImageComponents : blockedImageComponents;
  return (
    <div className={className}>
      <Markdown components={components} remarkPlugins={[remarkGfm]}>
        {stripLeadingHeading ? withoutLeadingHeading(visibleContent) : visibleContent}
      </Markdown>
    </div>
  );
}
