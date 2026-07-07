"""kernel HookManager 的 interceptor 半边（architecture.md:78-85，M4）。

确定性核心走 TDD。被测不变量：
- ``run_before`` 按**注册序**折叠 interceptor：每个可改写入参（返回新值）或阻断（veto）。
- **改写生效**：interceptor 返回的新值被下一环 / 调用方看到；``mutated`` 标反映值是否变。
- **veto 阻断**：interceptor 抛 ``HookVeto`` → ``run_before`` 冒泡该 veto、绝不放行原值。
- **fail-closed（安全门失败绝不静默放行）**：interceptor 抛**非 veto** 异常 → 隔离（log + 发事件）
  但**不静默降级为放行**，而是转成 ``HookVeto`` 冒泡（沿用 M6"未知即 fail loud、宁挡勿放"）。
  mutation：把 except 分支改成放行原值 → ``test_interceptor_bug_is_fail_closed`` 必红。
- **observer 只读隔离**：observer 抛异常同 EventSink——被捕获 + 记录、不冒泡、不打断其它 observer /
  不影响返回值（mutation：不隔离 observer 异常 → ``test_observer_exception_is_isolated`` 必红）。
- 每次 ``run_before`` 发一条 ``HOOK_INVOKED`` 上事件脊柱，payload 含 point / mutated / vetoed。
- 确定性：无墙上时钟 / random；注册序即执行序（mutation：反转执行序 → 顺序断言必红）。
"""

import logging

import pytest

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.hooks import HookManager, HookVeto


def _emitter_with_events() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="hook-test"), events


# 类型化的 interceptor（strict pyright 不接受无标注 lambda 参数）——挂点值类型为 str。
def _upper(v: str) -> str:
    return v.upper()


def _append_a(v: str) -> str:
    return v + "a"


def _append_b(v: str) -> str:
    return v + "b"


def _bang(v: str) -> str:
    return v + "!"


# --- 改写（interceptor 返回新值）------------------------------------------------


def test_interceptor_rewrites_value() -> None:
    hooks = HookManager()
    hooks.register_interceptor("p", _upper)
    emitter, events = _emitter_with_events()

    result = hooks.run_before("p", "abc", emitter=emitter)

    assert result == "ABC"
    invoked = next(e for e in events if e.type == EventType.HOOK_INVOKED)
    assert invoked.payload["point"] == "p"
    assert invoked.payload["mutated"] is True
    assert invoked.payload["vetoed"] is False


def test_no_interceptor_returns_value_unchanged_and_not_mutated() -> None:
    hooks = HookManager()
    emitter, events = _emitter_with_events()

    result = hooks.run_before("p", "abc", emitter=emitter)

    assert result == "abc"
    invoked = next(e for e in events if e.type == EventType.HOOK_INVOKED)
    # 无 interceptor：值不变，mutated=False（mutation：恒 True 标 → 此断言红）。
    assert invoked.payload["mutated"] is False


def test_interceptors_run_in_registration_order() -> None:
    hooks = HookManager()
    hooks.register_interceptor("p", _append_a)
    hooks.register_interceptor("p", _append_b)
    emitter, _ = _emitter_with_events()

    # 注册序即执行序：先 +a 再 +b（mutation：反转执行序 → 得 "xba"，断言红）。
    assert hooks.run_before("p", "x", emitter=emitter) == "xab"


def test_interceptor_bound_to_point_only() -> None:
    hooks = HookManager()
    hooks.register_interceptor("p", _upper)
    emitter, _ = _emitter_with_events()

    # 别的 point 不触发该 interceptor（按命名点隔离）。
    assert hooks.run_before("other", "abc", emitter=emitter) == "abc"


# --- veto 阻断（可阻断半的证明）------------------------------------------------


def test_veto_blocks_and_never_passes_through() -> None:
    def veto(_value: str) -> str:
        raise HookVeto("blocked")

    hooks = HookManager()
    hooks.register_interceptor("p", veto)
    emitter, events = _emitter_with_events()

    with pytest.raises(HookVeto):
        hooks.run_before("p", "abc", emitter=emitter)

    invoked = next(e for e in events if e.type == EventType.HOOK_INVOKED)
    assert invoked.payload["vetoed"] is True


def test_interceptor_bug_is_fail_closed() -> None:
    # 安全型 hook 抛非 veto 异常：不静默放行原值，转成 HookVeto 阻断（fail-closed）。
    def buggy(_value: str) -> str:
        raise RuntimeError("hook 内部 bug")

    hooks = HookManager()
    hooks.register_interceptor("p", buggy)
    emitter, events = _emitter_with_events()

    with pytest.raises(HookVeto):
        hooks.run_before("p", "抓取内容", emitter=emitter)

    invoked = next(e for e in events if e.type == EventType.HOOK_INVOKED)
    assert invoked.payload["vetoed"] is True
    # 事件留痕原始错误（可观测，不静默）。
    assert "RuntimeError" in invoked.payload["error"]


def test_interceptor_bug_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    def buggy(_value: str) -> str:
        raise RuntimeError("hook 内部 bug")

    hooks = HookManager()
    hooks.register_interceptor("p", buggy)
    emitter, _ = _emitter_with_events()

    with caplog.at_level(logging.ERROR), pytest.raises(HookVeto):
        hooks.run_before("p", "x", emitter=emitter)

    assert any("hook 内部 bug" in r.getMessage() or r.exc_info for r in caplog.records)


# --- observer 只读隔离（同 EventSink 语义）------------------------------------------------


def test_observer_sees_final_value() -> None:
    seen: list[str] = []
    hooks = HookManager()
    hooks.register_interceptor("p", _upper)
    hooks.register_observer("p", seen.append)
    emitter, _ = _emitter_with_events()

    result = hooks.run_before("p", "abc", emitter=emitter)

    assert result == "ABC"
    assert seen == ["ABC"]  # observer 看到 interceptor 改写后的终值


def test_observer_exception_is_isolated() -> None:
    calls: list[str] = []

    def boom(_value: str) -> None:
        raise RuntimeError("observer 崩了")

    hooks = HookManager()
    hooks.register_observer("p", boom)
    hooks.register_observer("p", calls.append)
    emitter, _ = _emitter_with_events()

    # observer 抛异常被隔离：返回值不受影响、后续 observer 仍被调（不冒泡、不打断）。
    result = hooks.run_before("p", "abc", emitter=emitter)

    assert result == "abc"
    assert calls == ["abc"]


# --- 确定性 ------------------------------------------------


def test_run_before_is_deterministic() -> None:
    hooks = HookManager()
    hooks.register_interceptor("p", _bang)
    emitter, _ = _emitter_with_events()

    # 同输入反复裁决同结果（无墙上时钟 / random 影响返回值）。
    assert hooks.run_before("p", "x", emitter=emitter) == "x!"
    assert hooks.run_before("p", "x", emitter=emitter) == "x!"
