"""Record / Replay 测试。

核心保证：录制一次对话后逐字节回放一致、不烧 token。键含 role + resolved model id，
basic=deepseek 与 enrich=qwen 的相同 messages 不撞键。
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.trace import Span, TraceStore
from grandquiz.providers.base import Completion, Message, Role, Usage
from grandquiz.providers.replay import (
    Cassette,
    RecordingProvider,
    ReplayMiss,
    ReplayProvider,
    replay_key,
)

_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}


class _CountingProvider:
    """确定性 inner provider：固定 text+usage，计自身被调次数（用于证明回放不触 inner）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return Completion(
            text=f"answer to: {last_user}",
            usage=Usage(prompt_tokens=11, completion_tokens=3),
        )


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


async def test_record_then_replay_is_byte_identical_and_burns_no_tokens(tmp_path: Path) -> None:
    cassette_path = tmp_path / "cassette.json"

    # Pass 1：录制。
    inner = _CountingProvider()
    cassette = Cassette()
    recording = RecordingProvider(inner, cassette, _MODELS)
    store1 = TraceStore(":memory:")
    sink1 = EventSink()
    sink1.subscribe(store1.record)
    runner1 = Runner(provider=recording, emitter=EventEmitter(sink1, ManualClock(), trace_id="t"))

    r1 = await runner1.run_turn("q1")
    r2 = await runner1.run_turn("q2")
    cassette.save(cassette_path)
    tree1 = store1.span_tree("t")
    assert inner.calls == 2

    # Pass 2：回放——全新 Runner + 重置 ManualClock + 相同输入。
    loaded = Cassette.load(cassette_path)
    replay = ReplayProvider(loaded, _MODELS)
    store2 = TraceStore(":memory:")
    sink2 = EventSink()
    sink2.subscribe(store2.record)
    runner2 = Runner(provider=replay, emitter=EventEmitter(sink2, ManualClock(), trace_id="t"))

    r1b = await runner2.run_turn("q1")
    r2b = await runner2.run_turn("q2")
    tree2 = store2.span_tree("t")

    # 逐字节一致的文本回放
    assert (r1, r2) == (r1b, r2b)
    # 回放没有多调一次 inner（pass 2 烧 0 token）
    assert inner.calls == 2
    # span 树结构 / 类型 / tokens / ts 全对齐（ManualClock 已重置）
    assert _summ(tree1) == _summ(tree2)
    store1.close()
    store2.close()


async def test_replay_returns_completion_identical_in_text_and_usage() -> None:
    inner = _CountingProvider()
    cassette = Cassette()
    recording = RecordingProvider(inner, cassette, _MODELS)
    msgs = [Message(role="user", content="hi")]

    original = await recording.complete(msgs, role="basic")
    replay = ReplayProvider(cassette, _MODELS)
    restored = await replay.complete(msgs, role="basic")

    assert restored.text == original.text
    assert restored.usage.model_dump() == original.usage.model_dump()
    assert restored.usage.total_tokens == original.usage.total_tokens
    assert inner.calls == 1  # 回放没有触碰 inner


def test_replay_key_includes_role_and_model() -> None:
    msgs = [Message(role="user", content="same")]
    k_basic_deepseek = replay_key(msgs, "basic", "deepseek-x")
    k_enrich_qwen = replay_key(msgs, "enrich", "qwen-x")
    k_basic_qwen = replay_key(msgs, "basic", "qwen-x")

    assert k_basic_deepseek != k_enrich_qwen
    assert k_basic_deepseek != k_basic_qwen
    assert k_enrich_qwen != k_basic_qwen


async def test_cross_model_lookup_misses() -> None:
    msgs = [Message(role="user", content="same")]
    inner = _CountingProvider()
    cassette = Cassette()
    recording = RecordingProvider(inner, cassette, _MODELS)
    await recording.complete(msgs, role="basic")  # 录在 (basic, deepseek-x)

    replay = ReplayProvider(cassette, _MODELS)
    with pytest.raises(ReplayMiss):
        await replay.complete(msgs, role="enrich")  # 问 (enrich, qwen-x)，同 messages → miss


async def test_replay_miss_on_unknown_key() -> None:
    replay = ReplayProvider(Cassette(), _MODELS)
    with pytest.raises(ReplayMiss):
        await replay.complete([Message(role="user", content="never recorded")], role="basic")
