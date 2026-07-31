"""一次判决的学习状态原子提交。"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from grandquiz.domain.learning.asked_questions import AskedQuestionsLedger
from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment_history import with_committed_state
from grandquiz.domain.learning.difficulty import (
    DifficultyEvidence,
    DifficultyLedger,
    DifficultyTier,
    DirectCorrectEvidence,
    DischargeEvidence,
    MasterySignals,
    ResetEvidence,
    difficulty_evolution_reason,
    evolve_difficulty,
)
from grandquiz.domain.learning.learning_facts import LearningFactEnvelope, LearningFactJournal
from grandquiz.domain.learning.memory import ConceptRecord, Memory, Transition
from grandquiz.domain.learning.persistence import LearningDatabase, TransactionParticipant


@runtime_checkable
class _CheckpointParticipant(Protocol):
    def _snapshot_state(self) -> object: ...
    def _restore_state(self, snapshot: object) -> None: ...


@dataclass(frozen=True)
class DifficultyChange:
    from_tier: DifficultyTier
    to_tier: DifficultyTier
    reason: str


@dataclass(frozen=True)
class JudgementCommit:
    transition: Transition
    difficulty_change: DifficultyChange | None
    learning_fact: LearningFactEnvelope | None = None


class LearningStateWriter:
    """隐藏多账本写入顺序，并为 SQLite/Dict 提供同一回滚语义。"""

    def __init__(
        self,
        *,
        memory: Memory,
        asked_questions: AskedQuestionsLedger | None,
        difficulty: DifficultyLedger | None,
        learning_facts: LearningFactJournal | None = None,
    ) -> None:
        self._memory = memory
        self._asked_questions = asked_questions
        self._difficulty = difficulty
        self._learning_facts = learning_facts
        self._participants = [
            participant
            for participant in (memory, asked_questions, difficulty, learning_facts)
            if participant is not None
        ]
        transaction_owners = {
            participant.transaction_owner
            for participant in self._participants
            if isinstance(participant, TransactionParticipant)
        }
        if len(transaction_owners) > 1:
            raise ValueError("学习状态 SQLite adapter 必须共享同一个 LearningDatabase")
        self._database = next(iter(transaction_owners), None)

    def commit_judgement(
        self,
        *,
        item_id: str,
        question: str,
        verdict: VerdictLabel,
        elapsed_ms: int,
        learning_fact: LearningFactEnvelope | None = None,
    ) -> JudgementCommit:
        with self._transaction():
            if self._asked_questions is not None:
                self._asked_questions.record_asked(item_id, question)
            before = self._memory.record_of(item_id) if self._difficulty is not None else None
            transition = self._memory.record_verdict(item_id, verdict)
            change = self._update_difficulty(
                item_id=item_id,
                verdict=verdict,
                transition=transition,
                before=before,
                elapsed_ms=elapsed_ms,
            )
            committed_fact = (
                with_committed_state(
                    learning_fact,
                    concept_state=transition.to_state,
                    difficulty_tier=(
                        self._difficulty.tier_of(item_id) if self._difficulty is not None else None
                    ),
                )
                if learning_fact is not None
                else None
            )
            if committed_fact is not None:
                if self._learning_facts is None:
                    raise ValueError("提交 learning_fact 必须配置 LearningFactJournal")
                self._learning_facts.append(committed_fact)
        return JudgementCommit(
            transition=transition,
            difficulty_change=change,
            learning_fact=committed_fact,
        )

    def _update_difficulty(
        self,
        *,
        item_id: str,
        verdict: VerdictLabel,
        transition: Transition,
        before: ConceptRecord | None,
        elapsed_ms: int,
    ) -> DifficultyChange | None:
        if self._difficulty is None:
            return None
        evidence = self._difficulty_evidence(
            verdict=verdict,
            transition=transition,
            before=before,
            elapsed_ms=elapsed_ms,
        )
        current = self._difficulty.progress_of(item_id)
        updated = evolve_difficulty(current, evidence)
        if updated == current:
            return None
        self._difficulty.set_progress(item_id, updated)
        if updated.tier == current.tier:
            return None
        return DifficultyChange(
            from_tier=current.tier,
            to_tier=updated.tier,
            reason=difficulty_evolution_reason(current, updated, evidence),
        )

    @staticmethod
    def _difficulty_evidence(
        *,
        verdict: VerdictLabel,
        transition: Transition,
        before: ConceptRecord | None,
        elapsed_ms: int,
    ) -> DifficultyEvidence:
        if transition.to_state == "销账" and before is not None:
            return DischargeEvidence(
                signals=MasterySignals(
                    rounds_to_discharge=len(before.verdict_history),
                    elapsed_ms=elapsed_ms,
                    had_struggle="勉强" in before.verdict_history,
                )
            )
        if verdict == "对" and before is None:
            return DirectCorrectEvidence()
        return ResetEvidence()

    @contextmanager
    def _transaction(self) -> Generator[None]:
        if isinstance(self._database, LearningDatabase):
            with self._database.transaction():
                yield
            return

        checkpointed = [
            participant
            for participant in self._participants
            if isinstance(participant, _CheckpointParticipant)
        ]
        snapshots = [
            (
                participant,
                participant._snapshot_state(),  # pyright: ignore[reportPrivateUsage]
            )
            for participant in checkpointed
        ]
        try:
            yield
        except Exception:
            for participant, snapshot in reversed(snapshots):
                participant._restore_state(snapshot)  # pyright: ignore[reportPrivateUsage]
            raise
