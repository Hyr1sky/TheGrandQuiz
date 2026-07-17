"""录制难度激活 capstone：连续直答正确升档，并以高档提示继续出题。

    uv run --env-file .env python scripts/record_difficulty_activation.py

同一 KnowledgeItem 连续跑三轮真实开放题：前两轮判对使难度 3→4，第三轮的出题请求必须带高档
难度提示。录下出题/判卷六个真实响应；回放测试若丢失升档或难度提示会因 key 不一致大声失败。
"""

import asyncio
from pathlib import Path

from grandquiz.domain.learning.asked_questions import DictAskedQuestionsLedger
from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.difficulty import DictDifficultyLedger
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = Path("tests/fixtures/difficulty_activation.cassette.json")
_ITEM_ID = "difficulty-activation-closure"
_ANSWER = (
    "闭包是函数连同定义时可访问的词法环境。它捕获外层变量本身，而不是创建时的值快照，"
    "所以外层函数返回后仍能访问该变量；变量后来被修改时，闭包会读到新值。"
    "例如循环里多个闭包若共享同一循环变量，调用时可能都看到最终值；可以通过每轮建立独立绑定来避免。"
)


def _stocked_store() -> LearningStore:
    store = LearningStore()
    resource = LearningResource(
        resource_id="difficulty-activation-resource",
        url="file://local/difficulty-activation",
    )
    item = KnowledgeItem(
        item_id=_ITEM_ID,
        resource_id=resource.resource_id,
        concept="闭包",
        summary="闭包让函数在外层作用域结束后继续访问其词法环境中的变量",
        evidence=[Evidence(quote="闭包捕获的是变量本身而非当时的值快照")],
        confidence=0.95,
    )
    store.replace_snapshot(resource, [item])
    return store


async def main() -> None:
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)
    memory = LearningMemory()
    asked = DictAskedQuestionsLedger()
    difficulty = DictDifficultyLedger()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="record-difficulty-activation")

    try:
        for round_index in range(3):
            result = await assess_once(
                store=_stocked_store(),
                provider=recording,
                responder=ScriptedResponder(answer=_ANSWER),
                memory=memory,
                emitter=emitter,
                rng=new_rng(round_index),
                question_type="开放",
                asked_questions=asked,
                difficulty=difficulty,
            )
            question = next(
                str(event.payload["question"])
                for event in reversed(events)
                if event.type == LearningEvent.QUESTION_ASKED
            )
            print(
                f"● 第 {round_index + 1} 轮：tier={difficulty.tier_of(_ITEM_ID)} "
                f"verdict={result.verdict} question={question}"
            )
            if result.verdict != "对":
                raise RuntimeError("真实判卷未给出‘对’，不能把本次录制当作难度激活证据")
    finally:
        await provider.aclose()

    changes = [event for event in events if event.type == LearningEvent.DIFFICULTY_TIER_CHANGED]
    if len(changes) != 1 or changes[0].payload.get("to_tier") != 4:
        raise RuntimeError("未观察到唯一一次 3→4 难度变化，拒绝保存不完整 cassette")

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)
    print(f"\ncassette 已存：{_FIXTURE}")
    print(f"● 难度变化：{changes[0].payload}")


if __name__ == "__main__":
    asyncio.run(main())
