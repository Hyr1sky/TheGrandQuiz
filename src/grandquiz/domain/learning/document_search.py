"""DocumentNode 的 current-revision 稀疏检索、树导航与预算内读取（ADR-0008 / DS-S4）。"""

import hashlib
import re
from typing import Literal, Protocol, Self

from pydantic import BaseModel, Field, model_validator

from grandquiz.domain.learning.citations import (
    CitationResolutionError,
    ResolvedCitation,
    resolve_citation,
)
from grandquiz.domain.learning.models import (
    DocumentNode,
    Evidence,
    EvidenceLocator,
    LearningResource,
    ResourceRevision,
)
from grandquiz.kernel.recovery import ErrorClass

MAX_SEARCH_LIMIT = 20
MAX_EXPAND_DEPTH = 4
MAX_EXPAND_LIMIT = 50
MAX_NODE_READ_CHARS = 4_000


class SearchScope(BaseModel):
    """搜索范围：全库 current revisions，或精确 resource id 集合。"""

    mode: Literal["all", "selected"]
    resource_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _selected_requires_ids(self) -> Self:
        if self.mode == "selected" and not self.resource_ids:
            raise ValueError("selected scope 必须提供 resource_ids")
        if self.mode == "all" and self.resource_ids:
            raise ValueError("all scope 不能携 resource_ids")
        if len(set(self.resource_ids)) != len(self.resource_ids):
            raise ValueError("resource_ids 不能重复")
        return self


class ScopeResolutionError(ValueError):
    """显式 scope 无法解析；必须 fail closed，不能退回 all。"""

    error_class = ErrorClass.DEGRADED

    def __init__(self, unresolved_resource_ids: list[str]) -> None:
        self.unresolved_resource_ids = unresolved_resource_ids
        super().__init__(f"无法解析 selected scope：{', '.join(unresolved_resource_ids)}")


class ReadBudgetExceeded(ValueError):
    """本 turn 的节点正文累计读取预算已耗尽。"""

    error_class = ErrorClass.DEGRADED

    def __init__(self, *, used: int, requested: int, limit: int) -> None:
        self.used = used
        self.requested = requested
        self.limit = limit
        super().__init__(f"节点读取预算不足：已用 {used}，本次 {requested}，上限 {limit}")


class EvidenceNotReadError(ValueError):
    """citation span 未被本 turn 的有界 read 覆盖。"""

    error_class = ErrorClass.DEGRADED
    classification = "evidence_not_read"


class DocumentSearchHit(BaseModel):
    resource_id: str
    revision_id: str
    node_id: str
    kind: str
    section_path: str
    title: str | None = None
    excerpt: str
    score: float
    untrusted: bool = True


class NodeReadResult(BaseModel):
    resource_id: str
    revision_id: str
    node_id: str
    section_path: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    content: str
    has_more: bool
    budget_used: int = Field(ge=0)
    budget_limit: int = Field(gt=0)
    untrusted: bool = True


class DocumentSearchRepository(Protocol):
    def get_resource(self, resource_id: str) -> LearningResource | None: ...
    def current_revision(self, resource_id: str) -> ResourceRevision | None: ...
    def get_revision(self, revision_id: str) -> ResourceRevision | None: ...
    def document_outline(self, resource_id: str) -> list[DocumentNode]: ...
    def document_nodes(
        self, resource_id: str, *, revision_id: str | None = None
    ) -> list[DocumentNode]: ...
    def search_document_nodes(
        self,
        query: str,
        *,
        resource_ids: list[str] | None,
        limit: int,
    ) -> list[DocumentSearchHit]: ...


class DocumentSearch:
    """调用方不感知 SQLite FTS/树行细节的受控查询面。"""

    def __init__(self, repository: DocumentSearchRepository, *, turn_read_budget: int = 12_000):
        if turn_read_budget < 1:
            raise ValueError("turn_read_budget 至少为 1")
        self._repository = repository
        self._turn_read_budget = turn_read_budget
        self._read_usage: dict[str, int] = {}
        self._read_ranges: dict[str, list[tuple[str, int, int]]] = {}

    def outline(self, resource_id: str) -> list[DocumentNode]:
        self._require_current_resources([resource_id])
        return self._repository.document_outline(resource_id)

    def search(
        self,
        query: str,
        *,
        scope: SearchScope,
        limit: int = 5,
    ) -> list[DocumentSearchHit]:
        if not query.strip():
            raise ValueError("搜索 query 不能为空")
        if not compile_fts_query(query):
            raise ValueError("搜索 query 必须包含至少一个可检索词")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"搜索 limit 必须在 1..{MAX_SEARCH_LIMIT}")
        resource_ids = None
        if scope.mode == "selected":
            self._require_current_resources(scope.resource_ids)
            resource_ids = scope.resource_ids
        return self._repository.search_document_nodes(
            query,
            resource_ids=resource_ids,
            limit=limit,
        )

    def expand(
        self,
        resource_id: str,
        node_id: str,
        *,
        max_depth: int = 1,
        limit: int = 20,
    ) -> list[DocumentNode]:
        self._require_current_resources([resource_id])
        if not 1 <= max_depth <= MAX_EXPAND_DEPTH:
            raise ValueError(f"max_depth 必须在 1..{MAX_EXPAND_DEPTH}")
        if not 1 <= limit <= MAX_EXPAND_LIMIT:
            raise ValueError(f"limit 必须在 1..{MAX_EXPAND_LIMIT}")
        nodes = self._repository.document_nodes(resource_id)
        if node_id not in {node.node_id for node in nodes}:
            raise ScopeResolutionError([f"{resource_id}:{node_id}"])
        frontier = {node_id}
        selected: list[DocumentNode] = []
        for _ in range(max_depth):
            children = [node for node in nodes if node.parent_node_id in frontier]
            selected.extend(children)
            frontier = {node.node_id for node in children}
            if not frontier:
                break
        return sorted(selected, key=lambda node: (node.ordinal, node.node_id))[:limit]

    def read_node(
        self,
        resource_id: str,
        node_id: str,
        *,
        start: int = 0,
        max_chars: int = 2_000,
        budget_key: str,
    ) -> NodeReadResult:
        revision = self._require_current_resources([resource_id])[0]
        if start < 0:
            raise ValueError("start 不能小于 0")
        if not 1 <= max_chars <= MAX_NODE_READ_CHARS:
            raise ValueError(f"max_chars 必须在 1..{MAX_NODE_READ_CHARS}")
        node = next(
            (
                candidate
                for candidate in self._repository.document_nodes(resource_id)
                if candidate.node_id == node_id
            ),
            None,
        )
        if node is None:
            raise ScopeResolutionError([f"{resource_id}:{node_id}"])
        node_content = revision.raw_content[node.start_offset : node.end_offset]
        if start > len(node_content):
            raise ValueError("start 超出节点正文")
        end = min(len(node_content), start + max_chars)
        consumed = end - start
        used = self._read_usage.get(budget_key, 0)
        if used + consumed > self._turn_read_budget:
            raise ReadBudgetExceeded(
                used=used,
                requested=consumed,
                limit=self._turn_read_budget,
            )
        self._read_usage[budget_key] = used + consumed
        self._read_ranges.setdefault(budget_key, []).append((node.node_id, start, end))
        return NodeReadResult(
            resource_id=resource_id,
            revision_id=revision.revision_id,
            node_id=node.node_id,
            section_path=node.section_path,
            start_offset=node.start_offset + start,
            end_offset=node.start_offset + end,
            content=node_content[start:end],
            has_more=end < len(node_content),
            budget_used=used + consumed,
            budget_limit=self._turn_read_budget,
        )

    def cite_node(
        self,
        resource_id: str,
        node_id: str,
        *,
        start: int,
        end: int,
        quote: str,
        budget_key: str,
        context_chars: int = 240,
    ) -> ResolvedCitation:
        """把本 turn 已读取区间内的 node-local span 铸为可解析 citation。"""
        revision = self._require_current_resources([resource_id])[0]
        if not any(
            read_node_id == node_id and read_start <= start < end <= read_end
            for read_node_id, read_start, read_end in self._read_ranges.get(budget_key, [])
        ):
            raise EvidenceNotReadError("citation span 尚未由本 turn 的 read_document_node 读取")
        node = next(
            (
                candidate
                for candidate in self._repository.document_nodes(resource_id)
                if candidate.node_id == node_id
            ),
            None,
        )
        if node is None:
            raise ScopeResolutionError([f"{resource_id}:{node_id}"])
        node_content = revision.raw_content[node.start_offset : node.end_offset]
        if not (0 <= start < end <= len(node_content)):
            raise CitationResolutionError("span_out_of_bounds", "citation span 超出节点边界")
        actual_quote = node_content[start:end]
        if actual_quote != quote:
            raise CitationResolutionError(
                "quote_mismatch",
                "citation quote 与已读取 source span 不一致",
            )
        evidence = Evidence(
            quote=quote,
            locator=EvidenceLocator(
                revision_id=revision.revision_id,
                node_id=node.node_id,
                section_path=node.section_path,
                start_offset=node.start_offset + start,
                end_offset=node.start_offset + end,
                quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            ),
        )
        return resolve_citation(self._repository, evidence, context_chars=context_chars)

    def _require_current_resources(self, resource_ids: list[str]) -> list[ResourceRevision]:
        unresolved: list[str] = []
        revisions: list[ResourceRevision] = []
        for resource_id in resource_ids:
            resource = self._repository.get_resource(resource_id)
            revision = self._repository.current_revision(resource_id)
            if resource is None or revision is None:
                unresolved.append(resource_id)
            else:
                revisions.append(revision)
        if unresolved:
            raise ScopeResolutionError(unresolved)
        return revisions


def compile_fts_query(query: str) -> str:
    """把自然语言词元转为 literal FTS phrase，避免暴露 MATCH 操作符/语法错误。"""
    terms: list[str] = []
    for token in re.findall(r"[\w]+", query, flags=re.UNICODE):
        if any(_is_cjk(character) for character in token):
            cjk = "".join(character for character in token if _is_cjk(character))
            terms.extend(_cjk_terms(cjk))
            latin = "".join(character for character in token if not _is_cjk(character))
            if latin:
                terms.append(latin)
        else:
            terms.append(token)
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def fts_projection(text: str) -> str:
    """为 unicode61 补 CJK unigram/bigram 词元，不依赖外部分词器。"""
    cjk_terms: list[str] = []
    for sequence in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", text):
        cjk_terms.extend(_cjk_terms(sequence))
    return text if not cjk_terms else f"{text}\n{' '.join(cjk_terms)}"


def _cjk_terms(sequence: str) -> list[str]:
    if len(sequence) < 2:
        return [sequence] if sequence else []
    return [*sequence, *(sequence[index : index + 2] for index in range(len(sequence) - 1))]


def _is_cjk(character: str) -> bool:
    return "\u3400" <= character <= "\u4dbf" or "\u4e00" <= character <= "\u9fff"
