"""SH-S9: 阻塞 CLI 审批门展示候选并返回人工筛选子集。"""

from collections.abc import Callable

import pytest
from rich.console import Console

from grandquiz.domain.learning.approval import (
    APPROVAL_DECIDED,
    APPROVAL_REQUESTED,
    ApprovalCancelled,
)
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.interfaces.cli.approval import CliApprovalGate
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink


def _item(item_id: str, concept: str) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=item_id,
        resource_id="resource-1",
        concept=concept,
        summary=f"{concept} 摘要",
        evidence=[Evidence(quote=f"{concept} 证据", locator="section-1")],
        confidence=0.85,
    )


def _harness() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="approval"), events


def _answers(*values: str) -> Callable[[str], str]:
    replies = iter(values)
    return lambda _prompt: next(replies)


def test_cli_approval_displays_full_preview_and_keeps_selected_items() -> None:
    console = Console(record=True, width=100)
    input_fn = _answers("y", "n")
    emitter, events = _harness()
    candidates = [_item("item-1", "闭包"), _item("item-2", "事件循环")]

    approved = CliApprovalGate(console=console, input_fn=input_fn).request_approval(
        candidates, emitter=emitter, parent_span_id="ingest"
    )

    assert [item.item_id for item in approved] == ["item-1"]
    output = console.export_text()
    assert "闭包" in output
    assert "闭包 摘要" in output
    assert "闭包 证据" in output
    assert "section-1" in output
    assert "0.85" in output
    assert [event.type for event in events] == [APPROVAL_REQUESTED, APPROVAL_DECIDED]
    assert events[-1].payload == {
        "outcome": "approved",
        "candidate_count": 2,
        "approved_count": 1,
        "approved_item_ids": ["item-1"],
    }


def test_cli_approval_all_rejected_is_explicit() -> None:
    console = Console(record=True, width=100)
    input_fn = _answers("n")
    emitter, events = _harness()

    approved = CliApprovalGate(console=console, input_fn=input_fn).request_approval(
        [_item("item-1", "闭包")], emitter=emitter, parent_span_id=None
    )

    assert approved == []
    assert "未保留任何知识点" in console.export_text()
    assert events[-1].payload["outcome"] == "rejected_all"


def test_cli_approval_cancel_emits_decision_and_raises() -> None:
    console = Console(record=True, width=100)
    input_fn = _answers("q")
    emitter, events = _harness()

    with pytest.raises(ApprovalCancelled, match="用户取消审批"):
        CliApprovalGate(console=console, input_fn=input_fn).request_approval(
            [_item("item-1", "闭包")], emitter=emitter, parent_span_id="ingest"
        )

    assert events[-1].type == APPROVAL_DECIDED
    assert events[-1].payload == {
        "outcome": "cancelled",
        "candidate_count": 1,
        "approved_count": 0,
        "approved_item_ids": [],
    }
