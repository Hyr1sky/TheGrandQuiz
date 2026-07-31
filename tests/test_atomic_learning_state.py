"""SH-S5：判决产生的三类学习状态共享提交/回滚语义。"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from grandquiz.domain.learning.asked_questions import (
    DictAskedQuestionsLedger,
    SqliteAskedQuestionsLedger,
)
from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.difficulty import (
    DEFAULT_TIER,
    DictDifficultyLedger,
    DifficultyProgress,
    SqliteDifficultyLedger,
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory, SqliteLearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningDatabase
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.state import LearningStateWriter
from grandquiz.domain.learning.store import LearningStore, SqliteLearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Completion, Message, Role, Usage

_ITEM_ID = "item-1"
_QUOTE = "闭包捕获变量而非值"


class _FailingDictDifficulty(DictDifficultyLedger):
    def set_progress(self, item_id: str, progress: DifficultyProgress) -> None:
        super().set_progress(item_id, progress)
        raise RuntimeError("difficulty write failed")


class _FailingSqliteDifficulty(SqliteDifficultyLedger):
    def set_progress(self, item_id: str, progress: DifficultyProgress) -> None:
        super().set_progress(item_id, progress)
        raise RuntimeError("difficulty write failed")


def _prime_observing(memory: LearningMemory | SqliteLearningMemory) -> None:
    memory.record_verdict(_ITEM_ID, "错")
    memory.record_verdict(_ITEM_ID, "对")
    assert memory.state_of(_ITEM_ID) == "观察中"


def test_dict_state_rolls_back_all_ledgers_when_one_write_fails() -> None:
    memory = LearningMemory()
    asked = DictAskedQuestionsLedger()
    difficulty = _FailingDictDifficulty()
    _prime_observing(memory)

    with pytest.raises(RuntimeError, match="difficulty write failed"):
        LearningStateWriter(
            memory=memory, asked_questions=asked, difficulty=difficulty
        ).commit_judgement(
            item_id=_ITEM_ID,
            question="什么是闭包？",
            verdict="对",
            elapsed_ms=1_000,
        )

    assert memory.state_of(_ITEM_ID) == "观察中"
    assert memory.record_of(_ITEM_ID).verdict_history == ["错", "对"]  # type: ignore[union-attr]
    assert asked.asked_before(_ITEM_ID) == []
    assert difficulty.tier_of(_ITEM_ID) == DEFAULT_TIER


def test_sqlite_state_rolls_back_all_ledgers_when_one_write_fails(tmp_path: Path) -> None:
    database = LearningDatabase(tmp_path / "learning.db")
    resource = LearningResource(resource_id="resource-1", url="file://local/test")
    item = KnowledgeItem(
        item_id=_ITEM_ID,
        resource_id=resource.resource_id,
        concept="闭包",
        summary="摘要",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.8,
    )
    SqliteLearningStore(database).replace_snapshot(resource, [item])
    memory = SqliteLearningMemory(database)
    asked = SqliteAskedQuestionsLedger(database)
    difficulty = _FailingSqliteDifficulty(database)
    _prime_observing(memory)

    with pytest.raises(RuntimeError, match="difficulty write failed"):
        LearningStateWriter(
            memory=memory, asked_questions=asked, difficulty=difficulty
        ).commit_judgement(
            item_id=_ITEM_ID,
            question="什么是闭包？",
            verdict="对",
            elapsed_ms=1_000,
        )

    assert memory.state_of(_ITEM_ID) == "观察中"
    assert memory.record_of(_ITEM_ID).verdict_history == ["错", "对"]  # type: ignore[union-attr]
    assert asked.asked_before(_ITEM_ID) == []
    assert difficulty.tier_of(_ITEM_ID) == DEFAULT_TIER


def test_sqlite_state_uses_explicit_shared_transaction_owner(tmp_path: Path) -> None:
    first = LearningDatabase(tmp_path / "first.db")
    second = LearningDatabase(tmp_path / "second.db")
    memory = SqliteLearningMemory(first)
    asked = SqliteAskedQuestionsLedger(second)
    try:
        assert memory.transaction_owner is first
        assert asked.transaction_owner is second
        with pytest.raises(ValueError, match="必须共享同一个 LearningDatabase"):
            LearningStateWriter(memory=memory, asked_questions=asked, difficulty=None)
    finally:
        first.close()
        second.close()


class _OpenCorrectProvider:
    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        payload = (
            {
                "question": "请解释闭包",
                "expected_points": [
                    {
                        "point_id": "core",
                        "description": "说明闭包的核心含义",
                        "cited_evidence": _QUOTE,
                    }
                ],
                "reference_answer": _QUOTE,
                "cited_evidence": [_QUOTE],
            }
            if role == "enrich"
            else {
                "verdict": "对",
                "matched_points": ["core"],
                "missing_points": [],
                "diagnosis": "complete",
                "reason": "回答正确",
                "cited_evidence": [_QUOTE],
            }
        )
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=1, completion_tokens=1),
        )


async def test_assessment_emits_no_committed_state_events_after_rollback() -> None:
    store = LearningStore()
    resource = LearningResource.create(url="file://local/test")
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="闭包",
        summary="摘要",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.8,
    )
    store.replace_snapshot(resource, [item])
    memory = LearningMemory()
    memory.record_verdict(item.item_id, "错")
    memory.record_verdict(item.item_id, "对")
    asked = DictAskedQuestionsLedger()
    difficulty = _FailingDictDifficulty()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="atomic")

    with pytest.raises(RuntimeError, match="difficulty write failed"):
        await assess_once(
            store=store,
            provider=_OpenCorrectProvider(),
            responder=ScriptedResponder(answer="我的回答"),
            memory=memory,
            emitter=emitter,
            rng=new_rng(0),
            asked_questions=asked,
            difficulty=difficulty,
        )

    event_types = [event.type for event in events]
    assert LearningEvent.CONCEPT_STATE_CHANGED not in event_types
    assert LearningEvent.DIFFICULTY_TIER_CHANGED not in event_types
    assert memory.state_of(item.item_id) == "观察中"
    assert asked.asked_before(item.item_id) == []
