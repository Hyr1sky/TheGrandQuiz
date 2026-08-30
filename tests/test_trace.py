"""trace 测试。

缝 2（确定性核心单元）：``build_span_tree`` 是纯函数，手搓事件流断言 span 森林。
缝 1（事件 / trace 流）：真实 Runner 经 TraceStore 落 SQLite，再从库里重建 span 树。
另断言 kernel 泛型持久化它不认识的领域事件类型（payload 原样往返）。
"""

from collections.abc import Mapping
from typing import Any

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.trace import TraceStore, build_span_tree
from grandquiz.providers.echo import DemoEchoProvider


def _event(
    type_: str,
    seq: int,
    ts: float,
    *,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> AgentEvent:
    return AgentEvent(
        type=type_,
        seq=seq,
        ts=ts,
        trace_id="t",
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload=payload if payload is not None else {},
    )


def test_build_span_tree_nests_model_under_turn() -> None:
    events = [
        _event(EventType.TURN_STARTED, 0, 0.0, span_id="s0", payload={"user_message": "hi"}),
        _event(
            EventType.MODEL_STARTED,
            1,
            1.0,
            span_id="s1",
            parent_span_id="s0",
            payload={"messages": []},
        ),
        _event(
            EventType.MODEL_ENDED,
            2,
            2.0,
            span_id="s1",
            parent_span_id="s0",
            payload={"ok": True, "output": "echo", "usage": {"total_tokens": 7}},
        ),
        _event(EventType.TURN_ENDED, 3, 3.0, span_id="s0", payload={"ok": True}),
    ]

    roots = build_span_tree(events)

    assert len(roots) == 1
    turn = roots[0]
    assert turn.type == "turn"
    assert turn.parent_span_id is None
    assert turn.input == {"user_message": "hi"}
    assert turn.latency == 3.0
    assert len(turn.children) == 1
    model = turn.children[0]
    assert model.type == "model"
    assert model.parent_span_id == "s0"
    assert model.latency is not None and model.latency > 0
    assert model.tokens == 7


def test_build_span_tree_attaches_error_to_span() -> None:
    events = [
        _event(EventType.TURN_STARTED, 0, 0.0, span_id="s0"),
        _event(EventType.MODEL_STARTED, 1, 1.0, span_id="s1", parent_span_id="s0"),
        _event(
            EventType.ERROR,
            2,
            2.0,
            span_id="s1",
            parent_span_id="s0",
            payload={"error": "RuntimeError('boom')"},
        ),
        _event(
            EventType.MODEL_ENDED,
            3,
            3.0,
            span_id="s1",
            parent_span_id="s0",
            payload={"ok": False, "error": "RuntimeError('boom')"},
        ),
        _event(EventType.TURN_ENDED, 4, 4.0, span_id="s0", payload={"ok": False}),
    ]

    roots = build_span_tree(events)

    model = roots[0].children[0]
    assert model.error is not None
    assert model.error["error"] == "RuntimeError('boom')"
    assert model.output == {"ok": False, "error": "RuntimeError('boom')"}
    assert model.tokens is None


async def test_trace_store_db_round_trip_reconstructs_turn_model() -> None:
    store = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(store.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id="run-1")
    runner = Runner(provider=DemoEchoProvider(), emitter=emitter)

    await runner.run_turn("hello")

    roots = store.span_tree("run-1")
    assert len(roots) == 1
    turn = roots[0]
    assert turn.type == "turn"
    assert [c.type for c in turn.children] == ["model"]
    # 真实 turn 的 token 用量经 usage.total_tokens（computed_field）落 trace、被 Span 表面出来，
    # 不是只有手搓 payload 才有——守住"每 turn token 用量进 trace"这条验收。
    model = turn.children[0]
    assert model.tokens is not None and model.tokens > 0
    store.close()


def test_trace_store_persists_unknown_domain_event() -> None:
    store = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(store.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id="run-2")
    payload = {"item_id": "k1", "concept": "闭包", "confidence": 0.9, "evidence": ["原文片段"]}

    emitter.emit("learning.item_created", payload=payload)

    events = store.events("run-2")
    assert len(events) == 1
    assert events[0].type == "learning.item_created"
    # payload 原样往返，含中文与嵌套结构——证明 kernel 持久化它不认识的类型
    assert events[0].payload == payload
    store.close()


def test_interleaved_traces_keep_independent_contiguous_sequences() -> None:
    """SQLite 行号可以交错；``seq`` 的契约只在各自 ``trace_id`` 内成立。"""
    store = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(store.record)
    first = EventEmitter(sink, ManualClock(), trace_id="first")
    second = EventEmitter(sink, ManualClock(), trace_id="second")

    first.emit("first.started")
    second.emit("second.started")
    second.emit("second.ended")
    first.emit("first.ended")

    assert [event.seq for event in store.events("first")] == [0, 1]
    assert [event.seq for event in store.events("second")] == [0, 1]
    store.close()
