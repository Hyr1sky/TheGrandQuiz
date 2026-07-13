"""录制真实 LLM 的历史摘要（context compression 增量 3）到 cassette——手动跑看质量 + 产回放 fixture。

    uv run --env-file .env python scripts/record_summarize.py

hand-stock 两轮被挤出滑窗的对话（入库 + 一次考核问答），用真实 provider（role=basic）跑
``LLMSummarizer.summarize``，打印生成的摘要，落 cassette 到 tests/fixtures/。
"""

import asyncio
from pathlib import Path

from grandquiz.domain.learning.summarizer import LLMSummarizer
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Message
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = Path("tests/fixtures/summarize.cassette.json")

# 被挤出滑窗的两轮（user/assistant 交替）——真实 react 会话跨轮裁剪后的形状。
_EVICTED_TURNS = [
    Message(role="user", content="把 py.md 入库一下"),
    Message(role="assistant", content="好的，已经把 py.md 里的知识点入库了，共抽取 3 个概念。"),
    Message(role="user", content="考我一题"),
    Message(
        role="assistant",
        content="闭包的核心是：函数捕获了外层作用域变量的引用，而不是当时的值快照。你答对了！",
    ),
]


async def main() -> None:
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)

    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="record-summarize")

    summarizer = LLMSummarizer(recording, emitter)
    try:
        summary = await summarizer.summarize("", _EVICTED_TURNS)
    finally:
        await provider.aclose()

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)

    print(f"cassette 已存：{_FIXTURE}\n")
    print(f"● 折入轮次：{len(_EVICTED_TURNS) // 2} 轮")
    print(f"● 生成摘要：{summary}")


if __name__ == "__main__":
    asyncio.run(main())
