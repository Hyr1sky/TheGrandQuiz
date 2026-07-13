"""端到端回放测试——用真实录制的 cassette 逐字节回放干扰项质量评审，零 token、无网络。

cassette 由 scripts/record_judge_distractor.py 对真实 deepseek（role=basic）录制，复用 case14
真机录制里出现的真实 MC 题（"闭包捕获的是什么？"）。若改了 prompts/judge_distractor_plausibility.md
或下方场景常量，messages 变 → replay_key 变 → ReplayMiss，本测试会红——需重录的信号。

只断言结构性质（label 合法、rationale 非空），不断言具体判成哪一档——"值"这个干扰项是否真被判
"合理"是模型的主观质量判断，会随模型/措辞漂移，断言具体档位会让测试变脆；真要盯质量随时间的
变化，属于人工复核 cassette 内容，不是这个回放测试的职责。
"""

import json
from pathlib import Path
from typing import cast

from grandquiz.domain.learning.judge import DistractorLabel, judge_distractor
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.providers.base import Role
from grandquiz.providers.replay import Cassette, ReplayProvider

_CASSETTE = Path("tests/fixtures/judge_distractor.cassette.json")
# 必须与 scripts/record_judge_distractor.py 的场景常量一致，否则 messages 对不上、回放落空。
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
_VALID_LABELS: set[DistractorLabel] = {"合理干扰", "较弱干扰", "无效干扰"}


async def test_recorded_judge_replays_deterministically_without_live_calls() -> None:
    raw: dict[str, dict[str, str]] = json.loads(_CASSETTE.read_text(encoding="utf-8"))
    model_for_role = cast("dict[Role, str]", {e["role"]: e["model"] for e in raw.values()})
    replay = ReplayProvider(Cassette.load(_CASSETTE), model_for_role)
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="judge-replay")

    for distractor in _DISTRACTORS:
        verdict = await judge_distractor(
            _ITEM, _QUESTION, _CORRECT, distractor, provider=replay, emitter=emitter
        )
        assert verdict.label in _VALID_LABELS
        assert verdict.rationale  # 理由非空
