"""ResourceRevision 的确定性文档结构解析（ADR-0008）。"""

import hashlib
import re
from dataclasses import dataclass

from grandquiz.domain.learning.models import (
    DocumentNode,
    DocumentNodeKind,
    LearningResource,
    ResourceRevision,
    derive_id,
)

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+")
_TABLE_SEPARATOR = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}")

MAX_DOCUMENT_LEAF_CHARS = 16_000


@dataclass(frozen=True)
class DocumentSnapshot:
    """一次可原子提交的 revision 与完整节点集合。"""

    revision: ResourceRevision
    nodes: tuple[DocumentNode, ...]


def build_document_snapshot(resource: LearningResource) -> DocumentSnapshot | None:
    """从已抓取资源确定性建立 revision 与 Markdown section tree。"""
    if resource.raw_content is None or resource.content_hash is None:
        return None
    actual_hash = hashlib.sha256(resource.raw_content.encode("utf-8")).hexdigest()
    if actual_hash != resource.content_hash:
        raise ValueError(f"资源 {resource.resource_id} 的 content_hash 与 raw_content 不一致")
    revision = ResourceRevision.create(
        resource_id=resource.resource_id,
        content_hash=resource.content_hash,
        raw_content=resource.raw_content,
        trusted=resource.trusted,
    )
    nodes = _parse_markdown(revision)
    return DocumentSnapshot(revision=revision, nodes=tuple(nodes))


def _parse_markdown(revision: ResourceRevision) -> list[DocumentNode]:
    content = revision.raw_content
    root_id = derive_id(revision.revision_id, "document")
    root = DocumentNode(
        node_id=root_id,
        revision_id=revision.revision_id,
        parent_node_id=None,
        kind="document",
        ordinal=0,
        depth=0,
        title=None,
        section_path="",
        start_offset=0,
        end_offset=len(content),
        content_fingerprint=derive_id(content),
        synthetic=True,
    )

    lines: list[tuple[int, int, str, str]] = []
    headings: list[tuple[int, int, int, str]] = []
    offset = 0
    fence_marker: str | None = None
    for line in content.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        lines.append((offset, offset + len(line), candidate, line))
        stripped = candidate.lstrip()
        if fence_marker is not None:
            if stripped.startswith(fence_marker):
                fence_marker = None
        elif stripped.startswith(("```", "~~~")):
            fence_marker = stripped[:3]
        else:
            match = _HEADING.match(candidate)
            if match is not None:
                headings.append(
                    (offset, offset + len(line), len(match.group(1)), match.group(2).strip())
                )
        offset += len(line)

    sections: list[DocumentNode] = []
    stack: list[DocumentNode] = []
    for index, (start, _heading_end, level, title) in enumerate(headings):
        while stack and stack[-1].depth >= level:
            stack.pop()
        parent = stack[-1] if stack else root
        path_titles = [node.title for node in stack if node.title is not None]
        section_path = " > ".join([*path_titles, title])
        end = len(content)
        for next_start, _next_end, next_level, _next_title in headings[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        node = DocumentNode(
            node_id=derive_id(revision.revision_id, "section", str(start), title),
            revision_id=revision.revision_id,
            parent_node_id=parent.node_id,
            kind="section",
            ordinal=0,
            depth=level,
            title=title,
            section_path=section_path,
            start_offset=start,
            end_offset=end,
            content_fingerprint=derive_id(content[start:end]),
        )
        sections.append(node)
        stack.append(node)

    blocks: list[DocumentNode] = []
    index = 0
    while index < len(lines):
        start, end, text, _raw = lines[index]
        if _HEADING.match(text) is not None or not text.strip():
            index += 1
            continue

        stripped = text.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            block_start = start
            block_end = end
            index += 1
            while index < len(lines):
                _next_start, next_end, next_text, _next_raw = lines[index]
                block_end = next_end
                index += 1
                if next_text.lstrip().startswith(marker):
                    break
            blocks.extend(
                _bounded_block_nodes(revision, root, sections, "code", block_start, block_end)
            )
            continue

        block_start = start
        block_end = end
        block_lines = [text]
        index += 1
        while index < len(lines):
            _next_start, next_end, next_text, _next_raw = lines[index]
            if not next_text.strip() or _HEADING.match(next_text) is not None:
                break
            if next_text.lstrip().startswith(("```", "~~~")):
                break
            block_lines.append(next_text)
            block_end = next_end
            index += 1
        kind = _block_kind(block_lines)
        blocks.extend(_bounded_block_nodes(revision, root, sections, kind, block_start, block_end))

    ordered = [root, *sections, *blocks]
    ordered.sort(
        key=lambda node: (
            0 if node.kind == "document" else 1,
            node.start_offset,
            0 if node.kind == "section" else 1,
            node.depth,
            node.node_id,
        )
    )
    return [node.model_copy(update={"ordinal": ordinal}) for ordinal, node in enumerate(ordered)]


def _block_kind(lines: list[str]) -> DocumentNodeKind:
    if all(_LIST_ITEM.match(line) is not None for line in lines):
        return "list"
    if (
        len(lines) >= 2
        and all("|" in line for line in lines)
        and _TABLE_SEPARATOR.match(lines[1]) is not None
    ):
        return "table"
    return "paragraph"


def _block_node(
    revision: ResourceRevision,
    root: DocumentNode,
    sections: list[DocumentNode],
    kind: DocumentNodeKind,
    start: int,
    end: int,
    *,
    synthetic: bool,
) -> DocumentNode:
    candidates = [
        section for section in sections if section.start_offset <= start < section.end_offset
    ]
    parent = max(candidates, key=lambda section: section.depth, default=root)
    block_text = revision.raw_content[start:end]
    fingerprint = derive_id(block_text)
    return DocumentNode(
        node_id=derive_id(revision.revision_id, kind, str(start), fingerprint),
        revision_id=revision.revision_id,
        parent_node_id=parent.node_id,
        kind=kind,
        ordinal=0,
        depth=parent.depth + 1,
        title=None,
        section_path=parent.section_path,
        start_offset=start,
        end_offset=end,
        content_fingerprint=fingerprint,
        synthetic=synthetic,
    )


def _bounded_block_nodes(
    revision: ResourceRevision,
    root: DocumentNode,
    sections: list[DocumentNode],
    kind: DocumentNodeKind,
    start: int,
    end: int,
) -> list[DocumentNode]:
    ranges = _bounded_ranges(revision.raw_content, start, end)
    synthetic = len(ranges) > 1
    return [
        _block_node(
            revision,
            root,
            sections,
            kind,
            part_start,
            part_end,
            synthetic=synthetic,
        )
        for part_start, part_end in ranges
    ]


def _bounded_ranges(content: str, start: int, end: int) -> list[tuple[int, int]]:
    if end - start <= MAX_DOCUMENT_LEAF_CHARS:
        return [(start, end)]
    ranges: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > MAX_DOCUMENT_LEAF_CHARS:
        hard_end = cursor + MAX_DOCUMENT_LEAF_CHARS
        newline = content.rfind("\n", cursor, hard_end + 1)
        split = newline + 1 if newline >= cursor + MAX_DOCUMENT_LEAF_CHARS // 2 else hard_end
        ranges.append((cursor, split))
        cursor = split
    ranges.append((cursor, end))
    return ranges
