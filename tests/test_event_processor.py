"""Processor 管线 + 异常隔离缝（缝-3）。

闭掉 EventSink 不隔离订阅者异常的已知坑（当初 Rich markup 崩的根因）：一个订阅者 / processor
抛异常应被捕获 + 记录、不冒泡、不中断对其它订阅者的扇出与本轮。既有 ``subscribe(callable)``
仍可用（隔离对它同样生效），额外 ``register(processor)`` 给富消费者。
"""

import logging

import pytest

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import (
    AgentEvent,
    DurableProcessorError,
    EventEmitter,
    EventSink,
    EventType,
)


class _BoomProcessor:
    """故意抛异常的富 processor——每次 on_event 都炸。"""

    def __init__(self) -> None:
        self.calls = 0

    def on_event(self, event: AgentEvent) -> None:
        self.calls += 1
        raise RuntimeError("boom")


class _CollectingProcessor:
    """正常富 processor——收下每个事件。"""

    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)


def _boom_observer(event: AgentEvent) -> None:
    raise RuntimeError("observer boom")


def test_processor_exception_is_isolated_from_other_processors() -> None:
    boom = _BoomProcessor()
    good = _CollectingProcessor()
    sink = EventSink()
    sink.register(boom)
    sink.register(good)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")

    # publish 不得因坏 processor 抛异常而冒泡
    emitter.emit(EventType.TURN_STARTED)
    emitter.emit(EventType.TURN_ENDED)

    # 坏 processor 被调用过（每个事件都进了扇出），但正常 processor 仍收到全部事件
    assert boom.calls == 2
    assert [e.type for e in good.events] == [
        EventType.TURN_STARTED,
        EventType.TURN_ENDED,
    ]


def test_durable_processor_failure_propagates_without_notifying_ui_observers() -> None:
    sink = EventSink()
    sink.register_durable(_BoomProcessor())
    rendered: list[AgentEvent] = []
    sink.subscribe(rendered.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")

    with pytest.raises(DurableProcessorError) as captured:
        emitter.emit(EventType.TURN_STARTED)

    assert captured.value.event.type == EventType.TURN_STARTED
    assert rendered == []


def test_best_effort_ui_failure_does_not_undo_durable_processing() -> None:
    sink = EventSink()
    durable = _CollectingProcessor()
    sink.register_durable(durable)
    sink.subscribe(_boom_observer)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")

    event = emitter.emit(EventType.TURN_STARTED)

    assert durable.events == [event]


def test_subscribe_callable_is_also_isolated() -> None:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(_boom_observer)  # 向后兼容的 callable 订阅者，且照样隔离
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")

    emitter.emit(EventType.TURN_STARTED)

    assert [e.type for e in events] == [EventType.TURN_STARTED]


def test_processor_exception_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    sink = EventSink()
    sink.register(_BoomProcessor())
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")

    with caplog.at_level(logging.ERROR):
        emitter.emit(EventType.TURN_STARTED)

    assert any(record.levelno >= logging.ERROR for record in caplog.records)


class _ValueErrorProcessor:
    """抛非 RuntimeError 的 Exception——钉住隔离宽度是 ``except Exception``、非只 RuntimeError。

    真实实例即 ``rich.errors.MarkupError``（Exception 子类、非 RuntimeError）——本 issue 要闭的坑。
    把 ``except Exception`` 收窄成 ``except RuntimeError`` 的 mutation 会让本测试红。
    """

    def on_event(self, event: AgentEvent) -> None:
        raise ValueError("non-RuntimeError boom")


def test_isolation_catches_any_exception_not_just_runtimeerror() -> None:
    good = _CollectingProcessor()
    sink = EventSink()
    sink.register(_ValueErrorProcessor())
    sink.register(good)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")

    emitter.emit(EventType.TURN_STARTED)  # 不得因 ValueError 冒泡

    assert [e.type for e in good.events] == [EventType.TURN_STARTED]


class _KeyboardInterruptProcessor:
    """抛 BaseException（非 Exception）——KeyboardInterrupt/SystemExit 必须冒泡、不被吞掉。"""

    def on_event(self, event: AgentEvent) -> None:
        raise KeyboardInterrupt


def test_baseexception_propagates_and_is_not_swallowed() -> None:
    # 把 except Exception 放宽成 except BaseException 的 mutation 会让本测试红——Ctrl-C / 关停语义
    # 必须穿透，不能被当作"隔离掉的错误"吞掉。
    sink = EventSink()
    sink.register(_KeyboardInterruptProcessor())
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")

    with pytest.raises(KeyboardInterrupt):
        emitter.emit(EventType.TURN_STARTED)
