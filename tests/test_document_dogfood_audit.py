"""DS-S3/4 dogfood auditor：交叉验证持久 learning/trace 证据。"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.tools.document_search_tools import (
    CitationToolResult,
    DocumentReadResult,
    DocumentSearchResult,
    NodeCitationToolResult,
    make_document_search_tools,
)
from grandquiz.evals.document_dogfood import audit_ingest_dogfood, audit_search_dogfood
from grandquiz.interfaces.cli.app import main, run_ingest
from grandquiz.interfaces.cli.approval import CliApprovalGate
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Role, ToolSpec, Usage

_QUOTE = "事件是信封，trace 复用同一事件流。"


class _ReaderProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        request = json.loads(
            next(message.content for message in messages if message.role == "user")
        )
        node = next(
            candidate
            for candidate in request["untrusted_document_nodes"]
            if _QUOTE in candidate["content"]
        )
        start = node["content"].index(_QUOTE)
        return Completion(
            text=json.dumps(
                {
                    "topic": "Agent Runtime",
                    "candidates": [
                        {
                            "concept": "事件信封",
                            "summary": "事件脊柱被 trace 和 runtime 共同复用",
                            "evidence": [
                                {
                                    "node_key": node["node_key"],
                                    "start_offset": start,
                                    "end_offset": start + len(_QUOTE),
                                    "quote": _QUOTE,
                                }
                            ],
                            "confidence": 0.95,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=100, completion_tokens=20),
        )


def _sole_trace_id(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT DISTINCT trace_id FROM events").fetchall()
    assert len(rows) == 1
    return str(rows[0][0])


async def test_ingest_dogfood_audit_proves_human_grounded_current_snapshot(
    tmp_path: Path,
) -> None:
    material = tmp_path / "runtime.md"
    material.write_text(
        "# Runtime\n\n## Events\n\n" + _QUOTE + "\n\n## Recovery\n\n错误也是事件。\n",
        encoding="utf-8",
    )
    learning_db = tmp_path / "learning.db"
    trace_db = tmp_path / "trace.db"
    console = Console(record=True, width=100)

    await run_ingest(
        title="document dogfood",
        material_path=material,
        db_path=learning_db,
        provider=_ReaderProvider(),
        approval=CliApprovalGate(console=console, input_fn=lambda _prompt: "y"),
        console=console,
        trace_db_path=trace_db,
    )

    report = audit_ingest_dogfood(
        learning_db=learning_db,
        trace_db=trace_db,
        trace_id=_sole_trace_id(trace_db),
    )

    assert report.passed is True
    assert report.resource_id is not None
    assert report.revision_id is not None
    assert all(check.passed for check in report.checks)


async def test_ingest_dogfood_audit_rejects_scripted_approval(tmp_path: Path) -> None:
    material = tmp_path / "runtime.md"
    material.write_text("# Runtime\n\n" + _QUOTE, encoding="utf-8")
    learning_db = tmp_path / "learning.db"
    trace_db = tmp_path / "trace.db"
    console = Console(record=True, width=100)

    await run_ingest(
        title="not human dogfood",
        material_path=material,
        db_path=learning_db,
        provider=_ReaderProvider(),
        approval=ScriptedApprovalGate(keep=lambda _item: True),
        console=console,
        trace_db_path=trace_db,
    )

    report = audit_ingest_dogfood(
        learning_db=learning_db,
        trace_db=trace_db,
        trace_id=_sole_trace_id(trace_db),
    )

    assert report.passed is False
    source_check = next(check for check in report.checks if check.name == "human_cli_approval")
    assert source_check.passed is False
    assert "scripted" in source_check.detail


async def test_search_dogfood_audit_proves_scoped_progressive_read_before_cite(
    tmp_path: Path,
    capsys: Any,
) -> None:
    material = tmp_path / "runtime.md"
    material.write_text(
        "# Runtime\n\n## Events\n\n"
        + ("事件背景与可观测性。" * 120)
        + _QUOTE
        + ("事件背景与可恢复性。" * 120),
        encoding="utf-8",
    )
    learning_db = tmp_path / "learning.db"
    ingest_trace_db = tmp_path / "ingest-trace.db"
    console = Console(record=True, width=100)
    await run_ingest(
        title="search stock",
        material_path=material,
        db_path=learning_db,
        provider=_ReaderProvider(),
        approval=CliApprovalGate(console=console, input_fn=lambda _prompt: "y"),
        console=console,
        trace_db_path=ingest_trace_db,
    )
    ingest_trace_id = _sole_trace_id(ingest_trace_db)

    store = SqliteLearningStore(learning_db)
    resource = store.get_resource(store.all_items()[0].resource_id)
    assert resource is not None
    revision = store.current_revision(resource.resource_id)
    assert revision is not None
    registry = ToolRegistry()
    for tool in make_document_search_tools(store=store, turn_read_budget=500):
        registry.register(tool)
    trace_db = ingest_trace_db
    trace = TraceStore(trace_db)
    sink = EventSink()
    sink.register_durable(trace)
    emitter = EventEmitter(sink, ManualClock(), trace_id="search-dogfood")
    context = ToolContext(emitter=emitter, parent_span_id="tool")

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
    node = next(
        node for node in store.document_nodes(resource.resource_id) if node.node_id == hit.node_id
    )
    node_content = revision.raw_content[node.start_offset : node.end_offset]
    local_start = node_content.index(_QUOTE)
    read = DocumentReadResult.model_validate_json(
        await registry.dispatch(
            "read_document_node",
            {
                "resource_id": resource.resource_id,
                "node_id": node.node_id,
                "start": local_start,
                "max_chars": len(_QUOTE),
            },
            ctx=context,
        )
    )
    assert read.content == _QUOTE
    citation = NodeCitationToolResult.model_validate_json(
        await registry.dispatch(
            "resolve_node_citation",
            {
                "resource_id": resource.resource_id,
                "node_id": node.node_id,
                "start": local_start,
                "end": local_start + len(_QUOTE),
                "quote": _QUOTE,
            },
            ctx=context,
        )
    )
    assert citation.quote == _QUOTE
    trace.close()
    store.close()

    report = audit_search_dogfood(
        learning_db=learning_db,
        trace_db=trace_db,
        trace_id="search-dogfood",
    )

    assert report.passed is True
    assert report.resource_id == resource.resource_id
    assert report.revision_id == revision.revision_id
    assert all(check.passed for check in report.checks)

    too_strict = audit_search_dogfood(
        learning_db=learning_db,
        trace_db=trace_db,
        trace_id="search-dogfood",
        max_read_fraction=0.001,
    )
    assert too_strict.passed is False
    fraction_check = next(
        check for check in too_strict.checks if check.name == "progressive_read_fraction"
    )
    assert fraction_check.passed is False

    main(
        [
            "audit-doc",
            "--db",
            str(learning_db),
            "--trace-db",
            str(trace_db),
            "--ingest-trace",
            ingest_trace_id,
            "--search-trace",
            "search-dogfood",
        ]
    )
    assert '"passed": true' in capsys.readouterr().out


async def test_search_dogfood_audit_rejects_item_citation_substitution(
    tmp_path: Path,
) -> None:
    material = tmp_path / "runtime.md"
    material.write_text("# Runtime\n\n" + _QUOTE + ("补充背景。" * 100), encoding="utf-8")
    learning_db = tmp_path / "learning.db"
    trace_db = tmp_path / "trace.db"
    console = Console(record=True, width=100)
    await run_ingest(
        title="item citation is not search proof",
        material_path=material,
        db_path=learning_db,
        provider=_ReaderProvider(),
        approval=CliApprovalGate(console=console, input_fn=lambda _prompt: "y"),
        console=console,
        trace_db_path=trace_db,
    )

    store = SqliteLearningStore(learning_db)
    item = store.all_items()[0]
    registry = ToolRegistry()
    for tool in make_document_search_tools(store=store, turn_read_budget=500):
        registry.register(tool)
    trace = TraceStore(trace_db)
    sink = EventSink()
    sink.register_durable(trace)
    context = ToolContext(
        emitter=EventEmitter(sink, ManualClock(), trace_id="item-citation"),
        parent_span_id="tool",
    )
    citation = CitationToolResult.model_validate_json(
        await registry.dispatch(
            "resolve_item_citation",
            {"item_id": item.item_id, "evidence_index": 0},
            ctx=context,
        )
    )
    assert citation.quote == _QUOTE
    trace.close()
    store.close()

    report = audit_search_dogfood(
        learning_db=learning_db,
        trace_db=trace_db,
        trace_id="item-citation",
    )

    assert report.passed is False
    path_check = next(
        check for check in report.checks if check.name == "progressive_search_read_node_citation"
    )
    assert path_check.passed is False
