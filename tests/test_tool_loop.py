"""R1-S1：有界 tool-calling 循环（自由 ReAct 的机制层）——零 token 可 replay。

确定性核心走 TDD：工具注册表 dispatch / 循环终止 / max_iterations 大声失败 / span 嵌套 /
DEGRADED 回灌 vs FATAL 冒泡 / 记放一致。LLM 的"选工具"决策走 ReplayProvider，工具执行是确定
性代码、每趟重跑。这里唯一的工具是平凡确定的 echo（不进 kernel、不碰 domain）。
"""

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.hooks import HookManager
from grandquiz.kernel.recovery import ErrorClass, RecoveryPolicy, classify
from grandquiz.kernel.runner import MaxIterationsExceeded, Runner
from grandquiz.kernel.tools import ModelRetry, Tool, ToolRegistry
from grandquiz.kernel.trace import Span, TraceStore
from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    ProviderStreamEvent,
    Role,
    TextDelta,
    ToolCall,
    Usage,
    mark_malformed_arguments,
)
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayProvider

# --------------------------------------------------------------------------- #
# 平凡确定性工具：echo(text) -> "echoed:<text>"。组装点定义，不进 kernel、不碰 domain。
# --------------------------------------------------------------------------- #


class _EchoParams(BaseModel):
    text: str


def _echo_tool(calls: list[str]) -> Tool:
    async def handler(params: _EchoParams) -> str:
        calls.append(params.text)
        return f"echoed:{params.text}"

    return Tool(name="echo", description="回声 text", params=_EchoParams, handler=handler)


def _registry_with_echo() -> tuple[ToolRegistry, list[str]]:
    calls: list[str] = []
    registry = ToolRegistry()
    registry.register(_echo_tool(calls))
    return registry, calls


# --------------------------------------------------------------------------- #
# 脚本化 provider：无 tool 结果时发一次 echo tool_call；见到 tool 结果后收敛为 final 文本。
# --------------------------------------------------------------------------- #


class _ScriptedProvider:
    """确定性：首轮出 echo tool_call，回灌 tool 结果后出 final。计自身被调次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        tool_results = [m for m in messages if m.role == "tool"]
        if tool_results:
            return Completion(
                text=f"final: {tool_results[-1].content}",
                usage=Usage(prompt_tokens=7, completion_tokens=2),
            )
        return Completion(
            text="",
            tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})],
            usage=Usage(prompt_tokens=3, completion_tokens=1),
        )


class _AlwaysToolProvider:
    """永不收敛：每次都要求调 echo——用于逼出 max_iterations 大声失败。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        return Completion(
            text="",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="echo", arguments={"text": "x"})],
        )


class _FinalOnlyProvider:
    """从不出 tool_call，直接给 final 文本（无工具路径）。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        return Completion(text="just an answer", usage=Usage(prompt_tokens=2, completion_tokens=2))


class _StreamingFinalProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        del messages, role, tools
        raise AssertionError("Runner 应优先使用原生 stream_complete")

    def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        del messages, role, tools

        async def stream() -> AsyncIterator[ProviderStreamEvent]:
            yield TextDelta(text="正")
            yield TextDelta(text="考")
            yield TextDelta(text="级")
            yield CompletionFinished(
                completion=Completion(
                    text="正考级",
                    usage=Usage(
                        prompt_tokens=4,
                        completion_tokens=2,
                    ),
                )
            )

        return stream()


def _emitter_with_events() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


# --------------------------------------------------------------------------- #
# ToolRegistry / dispatch
# --------------------------------------------------------------------------- #


async def test_dispatch_validates_and_calls_handler() -> None:
    registry, calls = _registry_with_echo()
    result = await registry.dispatch("echo", {"text": "hi"})
    assert result == "echoed:hi"
    assert calls == ["hi"]


async def test_dispatch_unknown_tool_raises_degraded_model_retry() -> None:
    registry, _ = _registry_with_echo()
    with pytest.raises(ModelRetry) as ei:
        await registry.dispatch("nope", {})
    assert classify(ei.value) is ErrorClass.DEGRADED


async def test_dispatch_invalid_args_raises_degraded_model_retry() -> None:
    registry, calls = _registry_with_echo()
    with pytest.raises(ModelRetry) as ei:
        await registry.dispatch("echo", {})  # 缺 text
    assert classify(ei.value) is ErrorClass.DEGRADED
    assert calls == []  # 校验失败前绝不进 handler


async def test_dispatch_malformed_arguments_raises_degraded_model_retry() -> None:
    # provider 边界标记的"参数非法"态（畸形 JSON）→ dispatch 认出 → ModelRetry(DEGRADED)，与"合法但
    # 校验不过"同一条恢复路径。畸形参数绝不进 handler（不拿垃圾入参跑工具）。
    registry, calls = _registry_with_echo()
    with pytest.raises(ModelRetry) as ei:
        await registry.dispatch("echo", mark_malformed_arguments('{"text": "hi"'))
    assert classify(ei.value) is ErrorClass.DEGRADED
    assert calls == []


async def test_dispatch_malformed_args_rejected_even_for_no_field_tool() -> None:
    # 关键鲁棒性：无必填字段的工具（如 query_weak_concepts）若把畸形参数当合法空 dict，pydantic 会
    # 静默放行、静默跑工具——掩盖畸形。凭 sentinel 一律拒（DEGRADED），无论工具 schema 有无必填。
    class _NoParams(BaseModel):
        pass

    called: list[int] = []

    async def handler(_params: _NoParams) -> str:
        called.append(1)
        return "ok"

    registry = ToolRegistry()
    registry.register(Tool(name="noop", description="无入参", params=_NoParams, handler=handler))
    with pytest.raises(ModelRetry) as ei:
        await registry.dispatch("noop", mark_malformed_arguments("这根本不是 JSON"))
    assert classify(ei.value) is ErrorClass.DEGRADED
    assert called == []  # 畸形参数绝不触发 handler（不静默跑工具）


def test_register_rejects_duplicate_name() -> None:
    registry, _ = _registry_with_echo()
    with pytest.raises(ValueError):
        registry.register(_echo_tool([]))


# --------------------------------------------------------------------------- #
# run_agent_turn：终止 / 循环 / span 嵌套
# --------------------------------------------------------------------------- #


async def test_final_text_without_tools_terminates() -> None:
    emitter, events = _emitter_with_events()
    runner = Runner(provider=_FinalOnlyProvider(), emitter=emitter)
    reply = await runner.run_agent_turn("q")
    assert reply == "just an answer"
    assert [e.type for e in events] == [
        EventType.AGENT_TURN_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        EventType.AGENT_TURN_ENDED,
    ]


async def test_native_stream_emits_model_output_deltas_before_final_completion() -> None:
    emitter, events = _emitter_with_events()
    runner = Runner(provider=_StreamingFinalProvider(), emitter=emitter)

    reply = await runner.run_agent_turn("q")

    assert reply == "正考级"
    assert [event.type for event in events] == [
        EventType.AGENT_TURN_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_OUTPUT_DELTA,
        EventType.MODEL_OUTPUT_DELTA,
        EventType.MODEL_ENDED,
        EventType.AGENT_TURN_ENDED,
    ]
    assert [
        event.payload["text"] for event in events if event.type == EventType.MODEL_OUTPUT_DELTA
    ] == ["正", "考级"]


async def test_tool_loop_calls_tool_then_terminates() -> None:
    emitter, events = _emitter_with_events()
    registry, calls = _registry_with_echo()
    runner = Runner(provider=_ScriptedProvider(), emitter=emitter, tools=registry)
    reply = await runner.run_agent_turn("q")
    assert reply == "final: echoed:hi"
    assert calls == ["hi"]  # 工具确实被执行
    assert [e.type for e in events] == [
        EventType.AGENT_TURN_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        EventType.TOOL_CALL_STARTED,
        EventType.TOOL_CALL_ENDED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        EventType.AGENT_TURN_ENDED,
    ]


async def test_spans_nest_under_agent_turn() -> None:
    store = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(store.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")
    registry, _ = _registry_with_echo()
    runner = Runner(provider=_ScriptedProvider(), emitter=emitter, tools=registry)
    await runner.run_agent_turn("q")

    roots = store.span_tree("t")
    assert len(roots) == 1
    root = roots[0]
    assert root.type == "agent_turn"
    assert [c.type for c in root.children] == ["model", "tool_call", "model"]
    tool_span = root.children[1]
    assert tool_span.input["tool_name"] == "echo"
    assert tool_span.output is not None and tool_span.output["result"] == "echoed:hi"
    store.close()


async def test_max_iterations_fails_loudly() -> None:
    emitter, events = _emitter_with_events()
    registry, _ = _registry_with_echo()
    runner = Runner(
        provider=_AlwaysToolProvider(), emitter=emitter, tools=registry, max_iterations=3
    )
    with pytest.raises(MaxIterationsExceeded):
        await runner.run_agent_turn("q")
    # 大声失败而非静默截断：agent turn 以 ok=False 封口
    ended = [e for e in events if e.type == EventType.AGENT_TURN_ENDED]
    assert len(ended) == 1 and ended[0].payload["ok"] is False
    # 恰好跑满 max_iterations 次 MODEL 调用
    assert len([e for e in events if e.type == EventType.MODEL_STARTED]) == 3


# --------------------------------------------------------------------------- #
# 接住加硬层：M6 RecoveryPolicy（DEGRADED 回灌 / FATAL 冒泡）+ M4 HookManager 挂点
# --------------------------------------------------------------------------- #


class _RecoverableError(Exception):
    error_class = ErrorClass.DEGRADED


def _failing_then_ok_registry(fail_times: list[int]) -> ToolRegistry:
    """echo，但前 N 次抛 DEGRADED 错误（fail_times 是可变计数容器）。"""

    async def handler(params: _EchoParams) -> str:
        if fail_times:
            fail_times.pop()
            raise _RecoverableError("transient")
        return f"echoed:{params.text}"

    registry = ToolRegistry()
    registry.register(Tool(name="echo", description="回声", params=_EchoParams, handler=handler))
    return registry


class _RetryProvider:
    """出 echo tool_call；见到含 'error' 的 tool 结果就重试同样的 tool_call；见到正常结果收敛。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        tool_results = [m for m in messages if m.role == "tool"]
        if tool_results and "error" not in tool_results[-1].content:
            return Completion(text=f"final: {tool_results[-1].content}")
        return Completion(
            text="",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="echo", arguments={"text": "hi"})],
        )


async def test_degraded_tool_error_is_fed_back_and_recovers() -> None:
    emitter, events = _emitter_with_events()
    registry = _failing_then_ok_registry([1])  # 第一次调 echo 抛错，第二次成功
    runner = Runner(provider=_RetryProvider(), emitter=emitter, tools=registry, max_iterations=6)
    reply = await runner.run_agent_turn("q")
    assert reply == "final: echoed:hi"
    # RecoveryPolicy 裁了一次 SKIP（DEGRADED 回灌）
    decided = [e for e in events if e.type == EventType.RECOVERY_DECIDED]
    assert len(decided) == 1 and decided[0].payload["decision"] == "skip"


class _MalformedThenFinalProvider:
    """首轮出一个 arguments 畸形的 echo tool_call（provider 边界已标记"参数非法"）；见到回灌的
    错误 tool 结果后收敛 final。复现 dogfood "神了"：坏 tool_call 不该崩会话，应降级回灌后自愈。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        tool_results = [m for m in messages if m.role == "tool"]
        if tool_results:
            return Completion(text=f"final: {tool_results[-1].content}")
        return Completion(
            text="",
            tool_calls=[
                ToolCall(id="c1", name="echo", arguments=mark_malformed_arguments('{"text"'))
            ],
        )


async def test_malformed_tool_call_recovers_via_degraded_feedback() -> None:
    # 端到端：一轮里 LLM 吐畸形 tool_call → dispatch 拒（DEGRADED）→ RecoveryPolicy SKIP 回灌错误 →
    # LLM 下一轮收敛 final。绝不崩会话；echo handler 从未真跑（畸形参数被 dispatch 拦下）。
    emitter, events = _emitter_with_events()
    registry, calls = _registry_with_echo()
    runner = Runner(
        provider=_MalformedThenFinalProvider(), emitter=emitter, tools=registry, max_iterations=6
    )
    reply = await runner.run_agent_turn("q")
    assert reply.startswith("final: tool error:")  # 错误作为 tool 结果回灌
    assert calls == []  # 畸形入参从未抵达 handler
    decided = [e for e in events if e.type == EventType.RECOVERY_DECIDED]
    assert len(decided) == 1 and decided[0].payload["decision"] == "skip"
    ended = [e for e in events if e.type == EventType.AGENT_TURN_ENDED]
    assert len(ended) == 1 and ended[0].payload["ok"] is True  # 会话正常收敛、非崩溃


class _FatalProvider:
    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        return Completion(
            text="",
            tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "boom"})],
        )


async def test_fatal_tool_error_bubbles_and_closes_turn() -> None:
    emitter, events = _emitter_with_events()

    async def handler(params: _EchoParams) -> str:
        raise RuntimeError("untagged → FATAL")  # 未打标 → 默认 FATAL

    registry = ToolRegistry()
    registry.register(Tool(name="echo", description="回声", params=_EchoParams, handler=handler))
    runner = Runner(provider=_FatalProvider(), emitter=emitter, tools=registry)
    with pytest.raises(RuntimeError):
        await runner.run_agent_turn("q")
    types = [e.type for e in events]
    assert EventType.ERROR in types
    ended = [e for e in events if e.type == EventType.AGENT_TURN_ENDED]
    assert len(ended) == 1 and ended[0].payload["ok"] is False


async def test_hook_point_intercepts_tool_arguments() -> None:
    emitter, _ = _emitter_with_events()
    registry, calls = _registry_with_echo()
    hooks = HookManager()

    def upper(args: dict[str, Any]) -> dict[str, Any]:
        return {**args, "text": str(args["text"]).upper()}

    hooks.register_interceptor("tool_call", upper)
    runner = Runner(provider=_ScriptedProvider(), emitter=emitter, tools=registry, hooks=hooks)
    reply = await runner.run_agent_turn("q")
    # interceptor 改写的入参确实抵达 handler
    assert calls == ["HI"]
    assert reply == "final: echoed:HI"


async def test_explicit_recovery_policy_is_used() -> None:
    # 注入的 RecoveryPolicy 与默认行为等价——证明 run_agent_turn 用的是注入实例而非硬编码。
    emitter, events = _emitter_with_events()
    registry = _failing_then_ok_registry([1])
    runner = Runner(
        provider=_RetryProvider(),
        emitter=emitter,
        tools=registry,
        recovery=RecoveryPolicy(emitter),
        max_iterations=6,
    )
    reply = await runner.run_agent_turn("q")
    assert reply == "final: echoed:hi"
    assert any(e.type == EventType.RECOVERY_DECIDED for e in events)


# --------------------------------------------------------------------------- #
# 记放一致：tool_call 分支被录下即确定，回放逐字节一致、零 token、span 树对齐
# --------------------------------------------------------------------------- #

_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}


def _summ(spans: list[Span]) -> list[dict[str, Any]]:
    return [
        {
            "type": s.type,
            "start_ts": s.start_ts,
            "end_ts": s.end_ts,
            "tokens": s.tokens,
            "children": _summ(s.children),
        }
        for s in spans
    ]


async def test_record_then_replay_tool_loop_is_byte_identical(tmp_path: Path) -> None:
    cassette_path = tmp_path / "toolloop.json"

    # Pass 1：录制（inner 脚本化 provider 真跑，tool_call 决策进 cassette）。
    inner = _ScriptedProvider()
    cassette = Cassette()
    recording = RecordingProvider(inner, cassette, _MODELS)
    store1 = TraceStore(":memory:")
    sink1 = EventSink()
    sink1.subscribe(store1.record)
    reg1, calls1 = _registry_with_echo()
    runner1 = Runner(
        provider=recording,
        emitter=EventEmitter(sink1, ManualClock(), trace_id="t"),
        tools=reg1,
    )
    r1 = await runner1.run_agent_turn("q")
    cassette.save(cassette_path)
    tree1 = store1.span_tree("t")
    inner_calls_after_record = inner.calls
    assert inner_calls_after_record == 2  # 一次出 tool_call、一次出 final

    # Pass 2：回放——全新 Runner + 重置 ManualClock + 相同输入。工具照常重跑（确定性代码）。
    loaded = Cassette.load(cassette_path)
    replay = ReplayProvider(loaded, _MODELS)
    store2 = TraceStore(":memory:")
    sink2 = EventSink()
    sink2.subscribe(store2.record)
    reg2, calls2 = _registry_with_echo()
    runner2 = Runner(
        provider=replay,
        emitter=EventEmitter(sink2, ManualClock(), trace_id="t"),
        tools=reg2,
    )
    r2 = await runner2.run_agent_turn("q")
    tree2 = store2.span_tree("t")

    assert r1 == r2 == "final: echoed:hi"
    assert inner.calls == inner_calls_after_record  # 回放没有多触 inner（烧 0 token）
    assert calls1 == calls2 == ["hi"]  # 工具在两趟都跑，输入一致
    assert _summ(tree1) == _summ(tree2)  # span 树结构 / ts / tokens 全对齐
    store1.close()
    store2.close()


def test_cassette_roundtrips_tool_calls() -> None:
    cassette = Cassette()
    completion = Completion(
        text="",
        tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})],
        usage=Usage(prompt_tokens=3, completion_tokens=1),
    )
    cassette.put("k", completion, role="basic", model_id="deepseek-x")
    restored = cassette.get("k")
    assert restored is not None
    assert restored.tool_calls is not None
    assert restored.tool_calls[0].name == "echo"
    assert restored.tool_calls[0].arguments == {"text": "hi"}
