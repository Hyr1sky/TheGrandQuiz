"""端到端回放测试——用真实录制的 cassette 逐字节回放单题考核，零 token、无网络。

cassette 由 scripts/record_assess.py 对真实 qwen（出题/enrich）+ deepseek（判卷/basic）录制；
此处用 ReplayProvider 重放整条 assess_once（两槽落两条 cassette 键，靠 role+model 区分）。
若改了 prompts/{question_generate,answer_grade}.md 或下方场景常量，messages 变 → replay_key 变 →
ReplayMiss，本测试会红——即"prompt / 场景漂移需重录"的信号（golden fixture 的预期维护流）。

下方场景常量必须与 scripts/record_assess.py 保持一致（含 items 顺序），否则 messages 对不上、回放落空。
"""

import json
from pathlib import Path
from typing import cast

from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
    LearningTask,
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.providers.base import Role
from grandquiz.providers.replay import Cassette, ReplayProvider

_CASSETTE = Path("tests/fixtures/assess.cassette.json")
_URL = "https://example.com/sample"
_SEED = 42
_DEFAULT_ANSWER = "闭包就是函数记住了外层变量，函数返回后还能读写它。"
# 必须与 scripts/record_assess.py 的 _ITEMS 一致（含顺序）。
_ITEMS = [
    ("闭包", "能访问外层函数作用域变量的函数", "闭包捕获的是变量本身而非当时的值快照"),
    ("pass@k", "k 次尝试中至少成功一次", "pass@k means success in at least one of k attempts"),
]
_VERDICTS = {"对", "勉强", "错"}


def _stocked_store() -> tuple[LearningStore, LearningTask]:
    store = LearningStore()
    task = LearningTask.create("样例主题")
    resource = LearningResource.create(task_id=task.task_id, url=_URL)
    store.add_task(task)
    store.add_resource(resource)
    store.add_items(
        [
            KnowledgeItem.create(
                resource_id=resource.resource_id,
                index=index,
                concept=concept,
                summary=summary,
                evidence=[Evidence(quote=quote)],
                confidence=0.9,
            )
            for index, (concept, summary, quote) in enumerate(_ITEMS)
        ]
    )
    return store, task


async def test_recorded_assessment_replays_deterministically_without_live_calls() -> None:
    raw: dict[str, dict[str, str]] = json.loads(_CASSETTE.read_text(encoding="utf-8"))
    # 从 cassette 复原 role→model（录制时的真实模型），使 replay_key 对齐、无需 .env。
    model_for_role = cast("dict[Role, str]", {e["role"]: e["model"] for e in raw.values()})
    replay = ReplayProvider(Cassette.load(_CASSETTE), model_for_role)
    store, task = _stocked_store()
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="assess-replay")

    result = await assess_once(
        task,
        store=store,
        provider=replay,  # 纯回放：命中即返回、未命中 ReplayMiss；绝不触网、不烧 token
        responder=ScriptedResponder(answer=_DEFAULT_ANSWER),
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    assert result.status == "judged"
    assert result.verdict in _VERDICTS
    assert result.item_id is not None
    # 代码记账与 verdict 一致：错 / 勉强 → weak_item_id 为被考 item；对 → None。
    if result.verdict in {"错", "勉强"}:
        assert result.weak_item_id == result.item_id
    else:
        assert result.weak_item_id is None
