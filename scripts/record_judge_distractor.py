"""录制真实 LLM 的干扰项质量评审（Tier-2 骨架首个场景）到 cassette——手动运行看质量、产回放 fixture。

    uv run --env-file .env python scripts/record_judge_distractor.py

复用 case14 真机录制里出现的真实 MC 题（"闭包捕获的是什么？"，见
tests/fixtures/eval_case14_bulk_quiz.cassette.json），对它的三个干扰项各评一版，打印判定 + 理由，
落 cassette 到 tests/fixtures/。三个干扰项迷惑性明显不同（"值"是材料原文点名的经典误解，"内存
地址"/"函数名"跟闭包关系较远），用来看 judge 是否真的产出有区分度的判定，而非无脑一律"合理"。
"""

import asyncio
from pathlib import Path

from grandquiz.domain.learning.judge import judge_distractor
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = Path("tests/fixtures/judge_distractor.cassette.json")

# 逐字照 case14 真机录制的原文（见 eval_case14_bulk_quiz.cassette.json 里的真实 MC 题）。
_QUESTION = "闭包捕获的是什么？"
_CORRECT = "变量"
_DISTRACTORS = ["值", "内存地址", "函数名"]
_ITEM = KnowledgeItem.create(
    resource_id="res",
    index=0,
    concept="闭包",
    summary="能访问外层函数作用域变量的函数",
    evidence=[Evidence(quote="闭包捕获变量而非值")],
    confidence=0.9,
)


async def main() -> None:
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="record-judge")

    try:
        for distractor in _DISTRACTORS:
            verdict = await judge_distractor(
                _ITEM,
                _QUESTION,
                _CORRECT,
                distractor,
                provider=recording,
                emitter=emitter,
            )
            print(f"● 干扰项「{distractor}」→ {verdict.label}：{verdict.rationale}")
    finally:
        await provider.aclose()

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)
    print(f"\ncassette 已存：{_FIXTURE}")


if __name__ == "__main__":
    asyncio.run(main())
