"""SH-S8: 未进入薄弱台账的连续答对也能演化难度。"""

import json
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.difficulty import (
    DEFAULT_TIER,
    DictDifficultyLedger,
    DifficultyProgress,
    DirectCorrectEvidence,
    DischargeEvidence,
    MasterySignals,
    ResetEvidence,
    SqliteDifficultyLedger,
    evolve_difficulty,
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningDatabase
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.state import LearningStateWriter
from grandquiz.domain.learning.store import LearningStore, SqliteLearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Completion, Message, Role, Usage

_ITEM_ID = "item-1"
_QUOTE = "闭包捕获词法环境"


def test_evolve_difficulty_promotes_only_after_two_direct_corrects() -> None:
    first = evolve_difficulty(DifficultyProgress(), DirectCorrectEvidence())
    second = evolve_difficulty(first, DirectCorrectEvidence())

    assert first == DifficultyProgress(tier=DEFAULT_TIER, correct_streak=1)
    assert second == DifficultyProgress(tier=4, correct_streak=0)


def test_evolve_difficulty_reset_clears_direct_correct_streak() -> None:
    current = DifficultyProgress(tier=4, correct_streak=1)
    assert evolve_difficulty(current, ResetEvidence()) == DifficultyProgress(
        tier=4, correct_streak=0
    )


def test_difficulty_progress_rejects_already_mature_streak() -> None:
    with pytest.raises(ValueError, match="correct_streak"):
        DifficultyProgress(correct_streak=2)


def test_evolve_difficulty_discharge_reuses_mastery_rule_and_clears_streak() -> None:
    current = DifficultyProgress(tier=3, correct_streak=1)
    result = evolve_difficulty(
        current,
        DischargeEvidence(
            signals=MasterySignals(
                rounds_to_discharge=2,
                elapsed_ms=1_000,
                had_struggle=False,
            )
        ),
    )
    assert result == DifficultyProgress(tier=4, correct_streak=0)


def _sqlite_ledger(db_path: Path) -> tuple[LearningDatabase, SqliteDifficultyLedger]:
    database = LearningDatabase(db_path)
    resource = LearningResource(resource_id="resource-1", url="file://local/test")
    item = KnowledgeItem(
        item_id=_ITEM_ID,
        resource_id=resource.resource_id,
        concept="闭包",
        summary="摘要",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )
    SqliteLearningStore(database).replace_snapshot(resource, [item])
    return database, SqliteDifficultyLedger(database)


def test_dict_sqlite_progress_parity_and_cross_session_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "learning.db"
    database, sqlite_ledger = _sqlite_ledger(db_path)
    dict_ledger = DictDifficultyLedger()
    progress = DifficultyProgress(tier=4, correct_streak=1)

    dict_ledger.set_progress(_ITEM_ID, progress)
    sqlite_ledger.set_progress(_ITEM_ID, progress)
    assert sqlite_ledger.progress_of(_ITEM_ID) == dict_ledger.progress_of(_ITEM_ID)
    database.close()

    reopened = SqliteDifficultyLedger(db_path)
    assert reopened.progress_of(_ITEM_ID) == progress
    reopened.close()


@pytest.mark.parametrize("verdict", ["错", "勉强"])
def test_learning_state_writer_resets_streak_after_weak_verdict(
    verdict: VerdictLabel,
) -> None:
    memory = LearningMemory()
    difficulty = DictDifficultyLedger()
    writer = LearningStateWriter(memory=memory, asked_questions=None, difficulty=difficulty)
    writer.commit_judgement(item_id=_ITEM_ID, question="q1", verdict="对", elapsed_ms=1_000)

    writer.commit_judgement(item_id=_ITEM_ID, question="q2", verdict=verdict, elapsed_ms=1_000)

    assert difficulty.progress_of(_ITEM_ID) == DifficultyProgress(
        tier=DEFAULT_TIER, correct_streak=0
    )


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
                        "required_claims": ["说明闭包的核心含义"],
                        "cited_evidence": _QUOTE,
                    }
                ],
                "reference_answer": _QUOTE,
                "cited_evidence": [_QUOTE],
            }
            if role == "enrich"
            else {
                "verdict": "对",
                "point_assessments": [
                    {
                        "point_id": "core",
                        "label": "matched",
                        "answer_evidence_ids": [],
                        "claim_assessments": [
                            {
                                "claim_id": "core.claim_1",
                                "label": "matched",
                                "answer_evidence_ids": re.findall(
                                    r"^- \[(v1e\d+_\d+)\]",
                                    messages[-1].content,
                                    flags=re.MULTILINE,
                                ),
                                "reason": "测试用 claim 判定。",
                            }
                        ],
                        "reason": "测试用逐点评判。",
                    }
                ],
                "diagnosis": "complete",
                "reason": "回答正确",
                "cited_evidence": [_QUOTE],
            }
        )
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=1, completion_tokens=1),
        )


def _stocked_store() -> LearningStore:
    store = LearningStore()
    resource = LearningResource.create(url="file://local/test")
    item = KnowledgeItem(
        item_id=_ITEM_ID,
        resource_id=resource.resource_id,
        concept="闭包",
        summary="摘要",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )
    store.replace_snapshot(resource, [item])
    return store


async def test_assessment_emits_change_only_after_second_direct_correct() -> None:
    memory = LearningMemory()
    difficulty = DictDifficultyLedger()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="direct-correct")

    for seed in (0, 1):
        await assess_once(
            store=_stocked_store(),
            provider=_OpenCorrectProvider(),
            responder=ScriptedResponder(answer="我的回答"),
            memory=memory,
            emitter=emitter,
            rng=new_rng(seed),
            question_type="开放",
            difficulty=difficulty,
        )

    changed = [event for event in events if event.type == LearningEvent.DIFFICULTY_TIER_CHANGED]
    assert len(changed) == 1
    assert changed[0].payload["from_tier"] == DEFAULT_TIER
    assert changed[0].payload["to_tier"] == DEFAULT_TIER + 1
    assert "连续答对" in changed[0].payload["reason"]
    assert difficulty.progress_of(_ITEM_ID) == DifficultyProgress(tier=4, correct_streak=0)
