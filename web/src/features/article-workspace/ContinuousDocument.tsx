import { useEffect, useMemo } from "react";
import { SafeMarkdown } from "../../shared/components/SafeMarkdown";
import type { DocumentNodeSummary } from "./api";

interface ContinuousDocumentProps {
  content: string;
  outline: DocumentNodeSummary[];
  activeNodeId: string | null;
  onActiveNodeChange: (nodeId: string) => void;
}

const documentSectionId = (nodeId: string) =>
  `document-section-${nodeId}`;

export function ContinuousDocument({
  content,
  outline,
  activeNodeId,
  onActiveNodeChange,
}: ContinuousDocumentProps) {
  const sections = useMemo(() => {
    const ordered = [...outline]
      .filter((node) => node.kind === "section")
      .sort((left, right) => left.start_offset - right.start_offset);
    return ordered.map((node, index) => ({
      node,
      content: content.slice(
        node.start_offset,
        ordered[index + 1]?.start_offset ?? content.length,
      ),
    }));
  }, [content, outline]);

  useEffect(() => {
    if (!("IntersectionObserver" in globalThis)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top)[0];
        const nodeId = visible?.target.getAttribute("data-document-node-id");
        if (nodeId) onActiveNodeChange(nodeId);
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );
    const elements = sections
      .map(({ node }) => document.getElementById(documentSectionId(node.node_id)))
      .filter((element): element is HTMLElement => element !== null);
    elements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [onActiveNodeChange, sections]);

  if (sections.length === 0) {
    return <SafeMarkdown className="reading-markdown" content={content} />;
  }

  const preamble = content.slice(0, sections[0]?.node.start_offset ?? 0);
  return (
    <>
      {preamble.trim() ? (
        <SafeMarkdown
          className="reading-markdown"
          content={preamble}
          stripDocumentPreamble
        />
      ) : null}
      {sections.map((section, index) => (
        <section
          className="reading-section"
          data-active={activeNodeId === section.node.node_id ? "true" : undefined}
          data-document-node-id={section.node.node_id}
          id={documentSectionId(section.node.node_id)}
          key={section.node.node_id}
        >
          <SafeMarkdown
            className="reading-markdown"
            content={section.content}
            stripLeadingHeading={index === 0}
          />
        </section>
      ))}
    </>
  );
}
