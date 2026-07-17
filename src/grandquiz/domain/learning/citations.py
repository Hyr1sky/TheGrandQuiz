"""KnowledgeItem evidence 的确定性 grounding 与 citation 渲染（ADR-0008）。"""

import hashlib
import re
from typing import Protocol

from pydantic import BaseModel, Field

from grandquiz.domain.learning.document import DocumentSnapshot
from grandquiz.domain.learning.models import (
    DocumentNode,
    Evidence,
    EvidenceLocator,
    KnowledgeItem,
    LearningResource,
    ResourceRevision,
)


class GroundingError(ValueError):
    """quote 无法唯一、精确地锚定到 DocumentSnapshot。"""

    def __init__(self, classification: str, quote: str, detail: str) -> None:
        self.classification = classification
        self.quote_fingerprint = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        super().__init__(detail)


class CitationResolutionError(ValueError):
    """历史 citation 声明的 revision/node/span 已无法解析。"""

    def __init__(self, classification: str, detail: str) -> None:
        self.classification = classification
        super().__init__(detail)


class CitationStore(Protocol):
    """citation 解析所需的最小只读 store 面。"""

    def get_revision(self, revision_id: str) -> ResourceRevision | None: ...
    def document_nodes(
        self, resource_id: str, *, revision_id: str | None = None
    ) -> list[DocumentNode]: ...


class ResolvedCitation(BaseModel):
    """已对声明 revision 做过复核、可向用户展示的有界原文上下文。"""

    resource_id: str
    revision_id: str
    node_id: str
    section_path: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    quote: str
    context_start: int = Field(ge=0)
    context_end: int = Field(ge=0)
    context: str


def ground_items(snapshot: DocumentSnapshot, items: list[KnowledgeItem]) -> list[KnowledgeItem]:
    """把 Reader quote 唯一定位到 revision/node/span；歧义或缺失时 fail closed。"""
    grounded: list[KnowledgeItem] = []
    for item in items:
        evidence = [_ground_evidence(snapshot, candidate) for candidate in item.evidence]
        grounded.append(item.model_copy(update={"evidence": evidence}))
    return grounded


def _ground_evidence(snapshot: DocumentSnapshot, evidence: Evidence) -> Evidence:
    proposed_quote = evidence.quote
    matches = _find_all(snapshot.revision.raw_content, proposed_quote)
    if len(matches) != 1:
        reason = "未在原文出现" if not matches else f"在原文出现 {len(matches)} 次"
        classification = "quote_missing" if not matches else "quote_ambiguous"
        raise GroundingError(
            classification,
            proposed_quote,
            f"Evidence quote 无法唯一定位（{reason}）：{proposed_quote!r}",
        )
    start, end = matches[0]
    quote = snapshot.revision.raw_content[start:end]
    node = _smallest_containing_node(snapshot.nodes, start, end)
    if node is None:
        raise GroundingError(
            "cross_node",
            quote,
            f"Evidence quote 未落在单一 DocumentNode：{quote!r}",
        )
    locator = EvidenceLocator(
        revision_id=snapshot.revision.revision_id,
        node_id=node.node_id,
        section_path=node.section_path,
        start_offset=start,
        end_offset=end,
        quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
    )
    return Evidence(quote=quote, locator=locator)


def _find_all(content: str, quote: str) -> list[tuple[int, int]]:
    """定位空白规范化后的 quote，同时保留原文精确 source span。"""
    parts = quote.split()
    if not parts:
        return []
    if len(parts) == 1 and parts[0] == quote:
        matches: list[tuple[int, int]] = []
        cursor = 0
        while (start := content.find(quote, cursor)) >= 0:
            matches.append((start, start + len(quote)))
            cursor = start + 1
        return matches
    normalized_pattern = r"\s+".join(re.escape(part) for part in parts)
    pattern = re.compile(f"(?=({normalized_pattern}))")
    return [(match.start(1), match.end(1)) for match in pattern.finditer(content)]


def _smallest_containing_node(
    nodes: tuple[DocumentNode, ...], start: int, end: int
) -> DocumentNode | None:
    leaves = [node for node in nodes if node.kind not in {"document", "section"}]
    candidates = [node for node in leaves if node.start_offset <= start and end <= node.end_offset]
    if not candidates and any(
        node.start_offset < end and start < node.end_offset for node in leaves
    ):
        return None  # span 穿过一个或多个自然正文节点，不能退化锚到 section/root。
    if not candidates:
        candidates = [
            node for node in nodes if node.start_offset <= start and end <= node.end_offset
        ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda node: (
            node.end_offset - node.start_offset,
            1 if node.kind in {"document", "section"} else 0,
            -node.depth,
            node.ordinal,
        ),
    )


def render_citation(resource: LearningResource, evidence: Evidence) -> str:
    """把精确 locator 渲染为用户可读、可返回原文的稳定路径。"""
    locator = evidence.locator
    if not isinstance(locator, EvidenceLocator):
        return f"{resource.topic or resource.url}：{evidence.quote}"
    path = locator.section_path or "文档根"
    return (
        f"{resource.topic or resource.url}@{locator.revision_id} > {path} "
        f"[{locator.start_offset}:{locator.end_offset}]：{evidence.quote}"
    )


def resolve_citation(
    store: CitationStore,
    evidence: Evidence,
    *,
    context_chars: int = 240,
) -> ResolvedCitation:
    """严格按 locator 声明的历史 revision 解析引用，不追随 current pointer。"""
    if context_chars < 0:
        raise ValueError("context_chars 不能小于 0")
    locator = evidence.locator
    if not isinstance(locator, EvidenceLocator):
        raise CitationResolutionError("unresolved", "Evidence 没有可解析的精确 locator")
    revision = store.get_revision(locator.revision_id)
    if revision is None:
        raise CitationResolutionError("revision_missing", "Evidence 声明的 revision 不存在")
    nodes = {
        node.node_id: node
        for node in store.document_nodes(
            revision.resource_id,
            revision_id=revision.revision_id,
        )
    }
    node = nodes.get(locator.node_id)
    if node is None:
        raise CitationResolutionError("node_missing", "Evidence 声明的 node 不存在")
    _validate_evidence(revision, node, evidence, locator)
    context_start = max(node.start_offset, locator.start_offset - context_chars)
    context_end = min(node.end_offset, locator.end_offset + context_chars)
    return ResolvedCitation(
        resource_id=revision.resource_id,
        revision_id=revision.revision_id,
        node_id=node.node_id,
        section_path=locator.section_path,
        start_offset=locator.start_offset,
        end_offset=locator.end_offset,
        quote=evidence.quote,
        context_start=context_start,
        context_end=context_end,
        context=revision.raw_content[context_start:context_end],
    )


def validate_exact_evidence(snapshot: DocumentSnapshot, items: list[KnowledgeItem]) -> None:
    """验证新快照每条 Evidence 都精确指向本 revision 的 node/source span。"""
    nodes = {node.node_id: node for node in snapshot.nodes}
    for item in items:
        for evidence in item.evidence:
            locator = evidence.locator
            if not isinstance(locator, EvidenceLocator):
                raise CitationResolutionError(
                    "unresolved",
                    "新快照不能包含 unresolved Evidence",
                )
            if locator.revision_id != snapshot.revision.revision_id:
                raise CitationResolutionError(
                    "revision_mismatch",
                    "Evidence locator 不属于待提交 revision",
                )
            node = nodes.get(locator.node_id)
            if node is None:
                raise CitationResolutionError("node_missing", "Evidence locator 的 node 不存在")
            _validate_evidence(snapshot.revision, node, evidence, locator)


def _validate_evidence(
    revision: ResourceRevision,
    node: DocumentNode,
    evidence: Evidence,
    locator: EvidenceLocator,
) -> None:
    quote_hash = hashlib.sha256(evidence.quote.encode("utf-8")).hexdigest()
    if node.revision_id != revision.revision_id:
        raise CitationResolutionError("node_revision_mismatch", "node 不属于声明 revision")
    if node.section_path != locator.section_path:
        raise CitationResolutionError("section_path_mismatch", "section_path 与 node 不一致")
    if not (node.start_offset <= locator.start_offset < locator.end_offset <= node.end_offset):
        raise CitationResolutionError("span_out_of_bounds", "Evidence span 超出 node 边界")
    if revision.raw_content[locator.start_offset : locator.end_offset] != evidence.quote:
        raise CitationResolutionError("quote_mismatch", "Evidence quote 与声明 source span 不一致")
    if locator.quote_hash != quote_hash:
        raise CitationResolutionError("quote_hash_mismatch", "Evidence quote hash 不一致")
