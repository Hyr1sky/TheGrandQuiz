"""DS-S4 受控 ReAct 工具：渐进读取、严格 scope、trace 与精确 citation。"""

import hashlib
from pathlib import Path

import pytest

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.tools.document_search_tools import (
    CitationToolResult,
    DocumentOutlineResult,
    DocumentReadResult,
    DocumentSearchResult,
    NodeCitationToolResult,
    make_document_search_tools,
)
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.kernel.tools import ToolContext, ToolRegistry


def _stock(store: SqliteLearningStore) -> tuple[LearningResource, KnowledgeItem]:
    content = "# Runtime\n\n## Events\n\n事件是信封，trace 复用同一事件流。\n"
    resource = LearningResource.create(url="https://example.com/runtime").model_copy(
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
        concept="事件信封",
        summary="事件承载不透明 payload",
        evidence=[Evidence(quote="事件是信封")],
        confidence=0.9,
    )
    grounded = ground_items(document, [item])[0]
    store.replace_snapshot(resource, [grounded])
    return resource, grounded


def _registry(store: SqliteLearningStore, *, turn_read_budget: int = 100) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in make_document_search_tools(store=store, turn_read_budget=turn_read_budget):
        registry.register(tool)
    return registry


def _context() -> tuple[ToolContext, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="search-turn")
    return ToolContext(emitter=emitter, parent_span_id="tool"), events


async def test_progressive_tools_find_read_and_resolve_grounded_citation(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    resource, item = _stock(store)
    registry = _registry(store)
    context, events = _context()

    outline = DocumentOutlineResult.model_validate_json(
        await registry.dispatch(
            "list_document_outline",
            {"resource_id": resource.resource_id},
            ctx=context,
        )
    )
    assert [node.section_path for node in outline.nodes] == ["Runtime", "Runtime > Events"]
    assert all(node.untrusted for node in outline.nodes)
    assert "不可信" in outline.trust_notice

    searched = DocumentSearchResult.model_validate_json(
        await registry.dispatch(
            "search_document_nodes",
            {
                "query": "trace",
                "scope": {"mode": "selected", "resource_ids": [resource.resource_id]},
                "limit": 10,
            },
            ctx=context,
        )
    )
    hit = next(hit for hit in searched.hits if hit.kind == "paragraph")
    assert hit.untrusted is True
    assert "不可信" in searched.trust_notice
    with pytest.raises(ValueError, match="尚未由本 turn"):
        await registry.dispatch(
            "resolve_node_citation",
            {
                "resource_id": resource.resource_id,
                "node_id": hit.node_id,
                "start": 0,
                "end": 5,
                "quote": "trace",
            },
            ctx=context,
        )
    rejected = events[-1]
    assert rejected.type == LearningEvent.CITATION_REJECTED
    assert rejected.payload["classification"] == "evidence_not_read"
    assert "quote" not in rejected.payload
    assert len(rejected.payload["quote_fingerprint"]) == 64
    read = DocumentReadResult.model_validate_json(
        await registry.dispatch(
            "read_document_node",
            {"resource_id": resource.resource_id, "node_id": hit.node_id, "max_chars": 30},
            ctx=context,
        )
    )
    assert "trace" in read.content
    assert read.untrusted is True and "不可信" in read.trust_notice
    local_start = read.content.index("trace")
    node_citation = NodeCitationToolResult.model_validate_json(
        await registry.dispatch(
            "resolve_node_citation",
            {
                "resource_id": resource.resource_id,
                "node_id": hit.node_id,
                "start": local_start,
                "end": local_start + len("trace"),
                "quote": "trace",
                "context_chars": 20,
            },
            ctx=context,
        )
    )
    assert node_citation.quote == "trace"
    assert node_citation.quote in node_citation.context

    citation = CitationToolResult.model_validate_json(
        await registry.dispatch(
            "resolve_item_citation",
            {"item_id": item.item_id, "evidence_index": 0, "context_chars": 20},
            ctx=context,
        )
    )
    assert citation.quote == "事件是信封"
    assert citation.revision_id == store.current_revision(resource.resource_id).revision_id  # type: ignore[union-attr]
    assert citation.quote in citation.context
    assert [event.type for event in events] == [
        LearningEvent.DOCUMENT_OUTLINE_VIEWED,
        LearningEvent.DOCUMENT_NODES_SEARCHED,
        LearningEvent.CITATION_REJECTED,
        LearningEvent.DOCUMENT_NODE_READ,
        LearningEvent.CITATION_RESOLVED,
        LearningEvent.CITATION_RESOLVED,
    ]
    assert all(event.parent_span_id == "tool" for event in events)
    read_event = next(event for event in events if event.type == LearningEvent.DOCUMENT_NODE_READ)
    assert read_event.payload["budget_used"] == len(read.content)
    assert read_event.payload["budget_limit"] == 100
    store.close()


async def test_search_tool_rejects_unresolved_scope_without_search_event(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    registry = _registry(store)
    context, events = _context()

    with pytest.raises(ValueError, match="无法解析 selected scope"):
        await registry.dispatch(
            "search_document_nodes",
            {
                "query": "trace",
                "scope": {"mode": "selected", "resource_ids": ["missing"]},
            },
            ctx=context,
        )
    assert [event.type for event in events] == [LearningEvent.DOCUMENT_SEARCH_REJECTED]
    store.close()


async def test_read_budget_rejection_event_records_usage_and_limit(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    resource, _ = _stock(store)
    registry = _registry(store, turn_read_budget=5)
    context, events = _context()
    node = next(
        candidate
        for candidate in store.document_nodes(resource.resource_id)
        if candidate.kind == "paragraph"
    )

    with pytest.raises(ValueError, match="预算不足"):
        await registry.dispatch(
            "read_document_node",
            {"resource_id": resource.resource_id, "node_id": node.node_id, "max_chars": 6},
            ctx=context,
        )

    assert [event.type for event in events] == [LearningEvent.DOCUMENT_NODE_READ]
    assert events[0].payload == {
        "resource_id": resource.resource_id,
        "node_id": node.node_id,
        "ok": False,
        "reason": "budget_exceeded",
        "budget_used": 0,
        "budget_requested": 6,
        "budget_limit": 5,
    }
    store.close()


async def test_read_quote_mismatch_emits_structured_citation_rejection(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    resource, _ = _stock(store)
    registry = _registry(store)
    context, events = _context()
    node = next(
        candidate
        for candidate in store.document_nodes(resource.resource_id)
        if candidate.kind == "paragraph"
    )
    read = DocumentReadResult.model_validate_json(
        await registry.dispatch(
            "read_document_node",
            {"resource_id": resource.resource_id, "node_id": node.node_id, "max_chars": 30},
            ctx=context,
        )
    )
    start = read.content.index("trace")

    with pytest.raises(ValueError, match="quote"):
        await registry.dispatch(
            "resolve_node_citation",
            {
                "resource_id": resource.resource_id,
                "node_id": node.node_id,
                "start": start,
                "end": start + len("trace"),
                "quote": "tracz",
            },
            ctx=context,
        )

    assert [event.type for event in events] == [
        LearningEvent.DOCUMENT_NODE_READ,
        LearningEvent.CITATION_REJECTED,
    ]
    assert events[-1].payload["classification"] == "quote_mismatch"
    assert "quote" not in events[-1].payload
    store.close()


async def test_unresolved_item_citation_emits_structured_rejection(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    resource = LearningResource.create(url="https://example.com/unresolved-item")
    store.add_resource(resource)
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="旧知识",
        summary="仍可考但 citation 未解析",
        evidence=[Evidence(quote="旧证据")],
        confidence=0.8,
    )
    store.add_items([item])
    registry = _registry(store)
    context, events = _context()

    with pytest.raises(ValueError, match="精确 locator"):
        await registry.dispatch(
            "resolve_item_citation",
            {"item_id": item.item_id, "evidence_index": 0},
            ctx=context,
        )

    assert [event.type for event in events] == [LearningEvent.CITATION_REJECTED]
    assert events[0].payload == {
        "source": "knowledge_item",
        "item_id": item.item_id,
        "evidence_index": 0,
        "classification": "unresolved",
    }
    store.close()
