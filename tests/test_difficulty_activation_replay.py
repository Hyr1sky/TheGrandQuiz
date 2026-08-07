"""兼容行为回放：连续直答正确升档后，下一题必须走高档难度提示。

响应最初来自真实录制；答案 Evidence 单元 ID 生产化后，fixture 只把原响应的自由复制字段机械映射成
同一原文单元 ID 并迁移请求指纹。该 fixture 验证
难度状态机与回放路径，不作为新 Prompt 的质量或 Token 成本证据。
"""

import json
from pathlib import Path
from typing import cast

from grandquiz.domain.learning.asked_questions import DictAskedQuestionsLedger
from grandquiz.domain.learning.assessment.engine import AssessmentResult, assess_once
from grandquiz.domain.learning.difficulty import DictDifficultyLedger, DifficultyProgress
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Role
from grandquiz.providers.replay import Cassette, ReplayProvider

_CASSETTE = Path("tests/fixtures/difficulty_activation.cassette.json")
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


async def test_real_replay_activates_high_difficulty_after_direct_correct_streak() -> None:
    raw: dict[str, dict[str, object]] = json.loads(_CASSETTE.read_text(encoding="utf-8"))
    model_for_role = cast(
        "dict[Role, str]",
        {str(entry["role"]): str(entry["model"]) for entry in raw.values()},
    )
    replay = ReplayProvider(Cassette.load(_CASSETTE), model_for_role)
    memory = LearningMemory()
    asked = DictAskedQuestionsLedger()
    difficulty = DictDifficultyLedger()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="difficulty-activation-replay")

    results: list[AssessmentResult] = []
    for seed in range(3):
        results.append(
            await assess_once(
                store=_stocked_store(),
                provider=replay,
                responder=ScriptedResponder(answer=_ANSWER),
                memory=memory,
                emitter=emitter,
                rng=new_rng(seed),
                question_type="开放",
                asked_questions=asked,
                difficulty=difficulty,
            )
        )

    assert [result.verdict for result in results] == ["对", "对", "对"]
    questions = [
        str(event.payload["question"])
        for event in events
        if event.type == LearningEvent.QUESTION_ASKED
    ]
    assert len(questions) == 3
    assert len(set(questions)) == 3
    assert difficulty.progress_of(_ITEM_ID) == DifficultyProgress(tier=4, correct_streak=1)

    changed = [event for event in events if event.type == LearningEvent.DIFFICULTY_TIER_CHANGED]
    assert len(changed) == 1
    assert changed[0].payload["from_tier"] == 3
    assert changed[0].payload["to_tier"] == 4
    assert "连续答对" in changed[0].payload["reason"]
