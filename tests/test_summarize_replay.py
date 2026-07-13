"""端到端回放测试——用真实录制的 cassette 逐字节回放历史摘要，零 token、无网络。

cassette 由 scripts/record_summarize.py 对真实 deepseek（role=basic）录制。若改了
prompts/summarize.md 或下方场景常量（含 _EVICTED_TURNS 顺序），messages 变 → replay_key 变 →
ReplayMiss，本测试会红——即"prompt / 场景漂移需重录"的信号（golden fixture 的预期维护流）。
"""

import json
from pathlib import Path
from typing import cast

from grandquiz.domain.learning.summarizer import LLMSummarizer
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.providers.base import Message, Role
from grandquiz.providers.replay import Cassette, ReplayProvider

_CASSETTE = Path("tests/fixtures/summarize.cassette.json")
# 须与 scripts/record_summarize.py 的 _EVICTED_TURNS 一致（含顺序），否则回放落空。
_EVICTED_TURNS = [
    Message(role="user", content="把 py.md 入库一下"),
    Message(role="assistant", content="好的，已经把 py.md 里的知识点入库了，共抽取 3 个概念。"),
    Message(role="user", content="考我一题"),
    Message(
        role="assistant",
        content="闭包的核心是：函数捕获了外层作用域变量的引用，而不是当时的值快照。你答对了！",
    ),
]


async def test_recorded_summarize_replays_deterministically_without_live_calls() -> None:
    raw: dict[str, dict[str, str]] = json.loads(_CASSETTE.read_text(encoding="utf-8"))
    # 从 cassette 复原 role→model（录制时的真实模型），使 replay_key 对齐、无需 .env。
    model_for_role = cast("dict[Role, str]", {e["role"]: e["model"] for e in raw.values()})
    replay = ReplayProvider(Cassette.load(_CASSETTE), model_for_role)
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="summarize-replay")

    summarizer = LLMSummarizer(replay, emitter)  # 纯回放：命中即返回，绝不触网、不烧 token
    summary = await summarizer.summarize("", _EVICTED_TURNS)

    assert summary  # 非空——摘要不能是空串
    assert "py.md" in summary or "闭包" in summary  # 折入内容的关键信息被保留
