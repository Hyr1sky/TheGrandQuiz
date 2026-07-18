"""DS-S3/4 dogfood 的只读、可重复证据审计。

auditor 把持久化 ``trace.db`` 事件与 ``learning.db`` 当前快照交叉核对；它不迁移、不写库，也不把
“事件存在”误当成完成。报告由独立 checks 组成，任何缺失或矛盾都令 ``passed=False``。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.kernel.events import AgentEvent, EventType

_PROVIDER_TOKEN_LIMIT = 32_000


class DogfoodCheck(BaseModel):
    """一条独立、可展示的验收判断。"""

    name: str
    passed: bool
    detail: str


def _empty_checks() -> list[DogfoodCheck]:
    return []


class DogfoodAuditReport(BaseModel):
    """一次 trace/DB 交叉审计结果。"""

    kind: str
    trace_id: str
    passed: bool
    resource_id: str | None = None
    revision_id: str | None = None
    checks: list[DogfoodCheck] = Field(default_factory=_empty_checks)


class DocumentDogfoodAuditReport(BaseModel):
    """DS-S3 ingest 与 DS-S4 search 的联合验收结果。"""

    passed: bool
    ingest: DogfoodAuditReport
    search: DogfoodAuditReport


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _events(trace_db: Path, trace_id: str) -> list[AgentEvent]:
    with _readonly(trace_db) as connection:
        rows = connection.execute(
            "SELECT trace_id, seq, ts, type, span_id, parent_span_id, payload "
            "FROM events WHERE trace_id = ? ORDER BY seq",
            (trace_id,),
        ).fetchall()
    return [
        AgentEvent(
            trace_id=str(row["trace_id"]),
            seq=int(row["seq"]),
            ts=float(row["ts"]),
            type=str(row["type"]),
            span_id=None if row["span_id"] is None else str(row["span_id"]),
            parent_span_id=(None if row["parent_span_id"] is None else str(row["parent_span_id"])),
            payload=json.loads(str(row["payload"])),
        )
        for row in rows
    ]


def _check(name: str, passed: bool, detail: str) -> DogfoodCheck:
    return DogfoodCheck(name=name, passed=passed, detail=detail)


def _first(events: Iterable[AgentEvent], event_type: str) -> AgentEvent | None:
    return next((event for event in events if event.type == event_type), None)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ingest_snapshot_checks(
    learning_db: Path,
    *,
    resource_id: str,
    revision_id: str,
    approved_count: int,
    traced_node_ids: list[str],
) -> list[DogfoodCheck]:
    checks: list[DogfoodCheck] = []
    with _readonly(learning_db) as connection:
        resource = connection.execute(
            "SELECT current_revision_id FROM resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        current = None if resource is None else str(resource["current_revision_id"])
        checks.append(
            _check(
                "current_revision_committed",
                current == revision_id,
                f"trace revision={revision_id}, DB current={current}",
            )
        )
        revision = connection.execute(
            "SELECT raw_content FROM resource_revisions WHERE revision_id = ? AND resource_id = ?",
            (revision_id, resource_id),
        ).fetchone()
        raw_content = None if revision is None else str(revision["raw_content"])
        node_rows = connection.execute(
            "SELECT node_id, kind, ordinal, start_offset, end_offset "
            "FROM document_nodes WHERE revision_id = ? ORDER BY ordinal, node_id",
            (revision_id,),
        ).fetchall()
        expected_node_ids = [
            str(row["node_id"])
            for row in node_rows
            if str(row["kind"]) not in {"document", "section"}
            and raw_content is not None
            and raw_content[int(row["start_offset"]) : int(row["end_offset"])].strip()
        ]
        checks.append(
            _check(
                "reader_nodes_exactly_once",
                traced_node_ids == expected_node_ids
                and len(traced_node_ids) == len(set(traced_node_ids)),
                f"trace={len(traced_node_ids)}, expected={len(expected_node_ids)}",
            )
        )
        item_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_items WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()[0]
        )
        checks.append(
            _check(
                "approved_snapshot_count",
                item_count == approved_count and approved_count > 0,
                f"approved={approved_count}, current items={item_count}",
            )
        )
        evidence_rows = connection.execute(
            "SELECT e.quote, e.quote_hash, e.revision_id, e.node_id, e.start_offset, "
            "e.end_offset, e.resolved, n.revision_id AS node_revision, "
            "n.start_offset AS node_start, n.end_offset AS node_end "
            "FROM knowledge_items i JOIN knowledge_item_evidence e ON e.item_id = i.item_id "
            "LEFT JOIN document_nodes n ON n.node_id = e.node_id "
            "WHERE i.resource_id = ? ORDER BY i.item_id, e.ordinal",
            (resource_id,),
        ).fetchall()
        exact = bool(evidence_rows) and raw_content is not None
        if raw_content is not None:
            for row in evidence_rows:
                start_obj: object = row["start_offset"]
                end_obj: object = row["end_offset"]
                quote = str(row["quote"])
                located = start_obj is not None and end_obj is not None
                start = -1 if start_obj is None else int(str(start_obj))
                end = -1 if end_obj is None else int(str(end_obj))
                exact = exact and (
                    int(str(row["resolved"])) == 1
                    and str(row["revision_id"]) == revision_id
                    and str(row["node_revision"]) == revision_id
                    and located
                    and int(str(row["node_start"])) <= start < end <= int(str(row["node_end"]))
                    and raw_content[start:end] == quote
                    and hashlib.sha256(quote.encode("utf-8")).hexdigest() == str(row["quote_hash"])
                )
        checks.append(
            _check(
                "current_evidence_exact",
                exact,
                f"verified evidence rows={len(evidence_rows)}",
            )
        )
    return checks


def audit_ingest_dogfood(*, learning_db: Path, trace_db: Path, trace_id: str) -> DogfoodAuditReport:
    """证明一次真实 CLI Reader ingest 满足 DS-S3 的可观测与持久化不变量。"""
    events = _events(trace_db, trace_id)
    checks: list[DogfoodCheck] = []
    required = {
        LearningEvent.DOCUMENT_PARSED,
        LearningEvent.READER_BATCH_STARTED,
        LearningEvent.READER_BATCH_ENDED,
        LearningEvent.CITATION_VALIDATED,
        "approval.requested",
        "approval.decided",
        LearningEvent.REVISION_COMMITTED,
    }
    types = {event.type for event in events}
    missing = sorted(required - types)
    checks.append(_check("required_ingest_events", not missing, f"missing={missing}"))

    decided = _first(events, "approval.decided")
    human = decided is not None and decided.payload.get("decision_source") == "human_cli"
    checks.append(
        _check(
            "human_cli_approval",
            human,
            "decision_source="
            + ("missing" if decided is None else str(decided.payload.get("decision_source"))),
        )
    )
    approved_count = (
        0 if decided is None else (_integer(decided.payload.get("approved_count")) or 0)
    )
    committed = _first(events, LearningEvent.REVISION_COMMITTED)
    resource_id = None if committed is None else committed.payload.get("resource_id")
    revision_id = None if committed is None else committed.payload.get("revision_id")
    resource_id = resource_id if isinstance(resource_id, str) else None
    revision_id = revision_id if isinstance(revision_id, str) else None

    ordered_types = [
        LearningEvent.CITATION_VALIDATED,
        "approval.requested",
        "approval.decided",
        LearningEvent.REVISION_COMMITTED,
    ]
    positions = {
        event_type: next((event.seq for event in events if event.type == event_type), -1)
        for event_type in ordered_types
    }
    ordered = all(positions[left] < positions[right] for left, right in pairwise(ordered_types))
    checks.append(_check("grounding_approval_commit_order", ordered, str(positions)))

    batches = [event for event in events if event.type == LearningEvent.READER_BATCH_STARTED]
    batch_ends = [event for event in events if event.type == LearningEvent.READER_BATCH_ENDED]
    paired_batches = (
        bool(batches)
        and len(batches) == len(batch_ends)
        and {event.span_id for event in batches} == {event.span_id for event in batch_ends}
        and all(event.payload.get("ok") is True for event in batch_ends)
    )
    checks.append(
        _check(
            "reader_batches_closed",
            paired_batches,
            f"started={len(batches)}, ended={len(batch_ends)}",
        )
    )
    traced_node_ids = [
        node_id
        for batch in batches
        for node_id in cast("list[str]", batch.payload.get("node_ids", []))
    ]
    estimates_ok = bool(batches) and all(
        (_integer(batch.payload.get("estimated_tokens")) or _PROVIDER_TOKEN_LIMIT + 1)
        <= (_integer(batch.payload.get("token_budget")) or 0)
        for batch in batches
    )
    checks.append(_check("reader_batch_budget", estimates_ok, f"batches={len(batches)}"))

    batch_spans = {batch.span_id for batch in batches if batch.span_id is not None}
    model_ended = [
        event
        for event in events
        if event.type == EventType.MODEL_ENDED and event.parent_span_id in batch_spans
    ]
    real_usage = bool(model_ended)
    max_prompt = 0
    for event in model_ended:
        usage_obj = event.payload.get("usage")
        if not isinstance(usage_obj, Mapping):
            real_usage = False
            continue
        usage = cast("Mapping[str, Any]", usage_obj)
        prompt_tokens = _integer(usage.get("prompt_tokens"))
        if prompt_tokens is None or prompt_tokens <= 0:
            real_usage = False
            continue
        max_prompt = max(max_prompt, prompt_tokens)
        real_usage = real_usage and prompt_tokens <= _PROVIDER_TOKEN_LIMIT
    checks.append(
        _check(
            "reader_model_usage_within_provider_gate",
            real_usage,
            f"calls={len(model_ended)}, max_prompt_tokens={max_prompt}",
        )
    )

    if resource_id is not None and revision_id is not None:
        checks.extend(
            _ingest_snapshot_checks(
                learning_db,
                resource_id=resource_id,
                revision_id=revision_id,
                approved_count=approved_count,
                traced_node_ids=traced_node_ids,
            )
        )
    else:
        checks.append(_check("committed_revision_identified", False, "revision event missing"))

    return DogfoodAuditReport(
        kind="ingest",
        trace_id=trace_id,
        resource_id=resource_id,
        revision_id=revision_id,
        checks=checks,
        passed=bool(checks) and all(check.passed for check in checks),
    )


def audit_search_dogfood(
    *,
    learning_db: Path,
    trace_db: Path,
    trace_id: str,
    max_read_fraction: float = 0.25,
) -> DogfoodAuditReport:
    """证明一次开放搜索使用 exact scope、有界读取与 read-before-cite。"""
    if not 0 < max_read_fraction < 1:
        raise ValueError("max_read_fraction 必须在 0..1 之间")
    events = _events(trace_db, trace_id)
    checks: list[DogfoodCheck] = []
    searched = [event for event in events if event.type == LearningEvent.DOCUMENT_NODES_SEARCHED]
    reads = [
        event
        for event in events
        if event.type == LearningEvent.DOCUMENT_NODE_READ and event.payload.get("ok") is True
    ]
    citations = [
        event
        for event in events
        if event.type == LearningEvent.CITATION_RESOLVED
        and event.payload.get("source") == "node_read"
    ]
    checks.append(
        _check(
            "progressive_search_read_node_citation",
            bool(searched and reads and citations),
            f"search={len(searched)}, read={len(reads)}, node_citation={len(citations)}",
        )
    )
    checks.append(
        _check(
            "no_scope_rejection",
            not any(event.type == LearningEvent.DOCUMENT_SEARCH_REJECTED for event in events),
            "search must not fail closed before the successful path",
        )
    )

    citation = citations[-1] if citations else None
    revision_id_obj = None if citation is None else citation.payload.get("revision_id")
    node_id_obj = None if citation is None else citation.payload.get("node_id")
    revision_id = revision_id_obj if isinstance(revision_id_obj, str) else None
    node_id = node_id_obj if isinstance(node_id_obj, str) else None
    cite_start = None if citation is None else _integer(citation.payload.get("start_offset"))
    cite_end = None if citation is None else _integer(citation.payload.get("end_offset"))

    covering_reads = [
        event
        for event in reads
        if node_id is not None
        and event.payload.get("node_id") == node_id
        and citation is not None
        and event.seq < citation.seq
        and cite_start is not None
        and cite_end is not None
        and (_integer(event.payload.get("start_offset")) or -1) <= cite_start
        and (_integer(event.payload.get("end_offset")) or -1) >= cite_end
    ]
    checks.append(
        _check(
            "read_before_cite",
            bool(covering_reads),
            "citation span must be covered by an earlier successful node read",
        )
    )
    navigation_before_read = bool(
        searched
        and covering_reads
        and citations
        and min(event.seq for event in searched) < min(event.seq for event in covering_reads)
    )
    checks.append(
        _check(
            "search_before_read_before_cite",
            navigation_before_read,
            "selected search must precede the covering read and node citation",
        )
    )

    budget_ok = bool(reads) and all(
        (_integer(event.payload.get("budget_used")) or -1)
        <= (_integer(event.payload.get("budget_limit")) or -2)
        for event in reads
    )
    checks.append(_check("read_budget_respected", budget_ok, f"successful reads={len(reads)}"))

    resource_id: str | None = None
    revision_chars = 0
    node_exact = False
    if (
        revision_id is not None
        and node_id is not None
        and cite_start is not None
        and cite_end is not None
    ):
        with _readonly(learning_db) as connection:
            row = connection.execute(
                "SELECT rr.resource_id, rr.raw_content, r.current_revision_id, "
                "n.start_offset AS node_start, n.end_offset AS node_end "
                "FROM resource_revisions rr JOIN resources r ON r.resource_id = rr.resource_id "
                "JOIN document_nodes n ON n.revision_id = rr.revision_id "
                "WHERE rr.revision_id = ? AND n.node_id = ?",
                (revision_id, node_id),
            ).fetchone()
        if row is not None:
            resource_id = str(row["resource_id"])
            raw_content = str(row["raw_content"])
            revision_chars = len(raw_content)
            node_exact = (
                str(row["current_revision_id"]) == revision_id
                and int(row["node_start"]) <= cite_start < cite_end <= int(row["node_end"])
                and bool(raw_content[cite_start:cite_end])
            )
    checks.append(
        _check(
            "citation_resolves_current_node",
            node_exact,
            f"resource={resource_id}, revision={revision_id}, node={node_id}",
        )
    )

    selected_scopes: list[list[str]] = []
    for event in searched:
        scope_obj = event.payload.get("scope")
        if not isinstance(scope_obj, Mapping):
            continue
        scope = cast("Mapping[str, object]", scope_obj)
        if scope.get("mode") != "selected":
            continue
        ids_obj = scope.get("resource_ids")
        if isinstance(ids_obj, list):
            raw_ids = cast("list[object]", ids_obj)
            if all(isinstance(item, str) for item in raw_ids):
                selected_scopes.append(cast("list[str]", raw_ids))
    exact_scope = (
        resource_id is not None
        and bool(selected_scopes)
        and all(scope == [resource_id] for scope in selected_scopes)
    )
    checks.append(
        _check(
            "exact_selected_scope",
            exact_scope,
            f"selected_scopes={selected_scopes}, cited_resource={resource_id}",
        )
    )

    read_chars = sum(_integer(event.payload.get("chars")) or 0 for event in reads)
    fraction = 1.0 if revision_chars <= 0 else read_chars / revision_chars
    checks.append(
        _check(
            "progressive_read_fraction",
            revision_chars > 0 and 0 < fraction <= max_read_fraction,
            f"read_chars={read_chars}, revision_chars={revision_chars}, fraction={fraction:.4f}",
        )
    )

    return DogfoodAuditReport(
        kind="search",
        trace_id=trace_id,
        resource_id=resource_id,
        revision_id=revision_id,
        checks=checks,
        passed=bool(checks) and all(check.passed for check in checks),
    )


def audit_document_dogfood(
    *,
    learning_db: Path,
    trace_db: Path,
    ingest_trace_id: str,
    search_trace_id: str,
    max_read_fraction: float = 0.25,
) -> DocumentDogfoodAuditReport:
    """一条调用同时审计 DS-S3 与 DS-S4；任一 slice 失败则整体失败。"""
    ingest = audit_ingest_dogfood(
        learning_db=learning_db,
        trace_db=trace_db,
        trace_id=ingest_trace_id,
    )
    search = audit_search_dogfood(
        learning_db=learning_db,
        trace_db=trace_db,
        trace_id=search_trace_id,
        max_read_fraction=max_read_fraction,
    )
    return DocumentDogfoodAuditReport(
        passed=ingest.passed and search.passed,
        ingest=ingest,
        search=search,
    )
