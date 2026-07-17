"""DS-S4 capstone：不用倾倒全文，完成 search → read → grounded citation。"""

import hashlib
from dataclasses import dataclass

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.document_search import DocumentSearch, SearchScope
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.store import SqliteLearningStore


@dataclass(frozen=True)
class DocumentSearchEvalReport:
    passed: bool
    full_document_chars: int
    read_chars: int
    candidate_count: int
    citation_quote: str | None
    failures: tuple[str, ...]


def run_document_search_capstone() -> DocumentSearchEvalReport:
    """规则型 capstone；固定长文中定位唯一证据并验证 read-before-cite。"""
    decoys = [f"## 普通章节 {index}\n\n这是第 {index} 段常规说明。\n" for index in range(120)]
    target_quote = "durable processor 失败必须阻断当前 turn"
    target = f"## 承重事件\n\n{target_quote}，不能被 observer 隔离。\n"
    content = "# Agent Runtime\n\n" + "\n".join([*decoys[:70], target, *decoys[70:]])
    resource = LearningResource.create(url="https://example.com/search-capstone").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Agent Runtime",
        }
    )
    document = build_document_snapshot(resource)
    assert document is not None
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="承重事件处理器",
        summary="持久化失败会阻断 turn",
        evidence=[Evidence(quote=target_quote)],
        confidence=0.95,
    )
    grounded = ground_items(document, [item])[0]
    store = SqliteLearningStore(":memory:")
    store.replace_snapshot(resource, [grounded])
    search = DocumentSearch(store, turn_read_budget=300)
    failures: list[str] = []
    hits = search.search(
        "durable processor",
        scope=SearchScope(mode="selected", resource_ids=[resource.resource_id]),
        limit=5,
    )
    leaf = next((hit for hit in hits if hit.kind == "paragraph"), None)
    read_chars = 0
    citation_quote: str | None = None
    if leaf is None:
        failures.append("稀疏搜索未命中目标正文节点")
    else:
        read = search.read_node(
            resource.resource_id,
            leaf.node_id,
            max_chars=200,
            budget_key="capstone",
        )
        read_chars = len(read.content)
        start = read.content.find(target_quote)
        if start < 0:
            failures.append("预算内节点正文不含目标 quote")
        else:
            citation = search.cite_node(
                resource.resource_id,
                leaf.node_id,
                start=start,
                end=start + len(target_quote),
                quote=target_quote,
                budget_key="capstone",
            )
            citation_quote = citation.quote
    if read_chars >= len(content) // 10:
        failures.append("读取超过全文 10%，未证明渐进式披露")
    if citation_quote != target_quote:
        failures.append("最终 citation 未精确解析目标 quote")
    store.close()
    return DocumentSearchEvalReport(
        passed=not failures,
        full_document_chars=len(content),
        read_chars=read_chars,
        candidate_count=len(hits),
        citation_quote=citation_quote,
        failures=tuple(failures),
    )
