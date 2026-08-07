"""Append-only verdict correction and deterministic learning-state reconciliation."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment_history import (
    VERDICT_CORRECTED,
    AssessmentAttemptV1,
    project_assessment_attempts,
    rebuild_learning_state,
    verdict_correction_fact,
)
from grandquiz.domain.learning.models import derive_id
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.kernel.clock import Clock


class AssessmentAttemptNotFound(LookupError):
    """The correction target is not present in the durable attempt journal."""


class VerdictCorrectionConflict(ValueError):
    """The same idempotency key was reused with a different correction payload."""


class VerdictCorrectionCommand(BaseModel):
    """Channel-neutral command accepted by manual and regraded corrections."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(min_length=1)
    final_verdict: VerdictLabel
    reason: str = Field(min_length=1)
    supplemental_answer: str | None = None

    @field_validator("request_id", "reason")
    @classmethod
    def _required_text_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("supplemental_answer")
    @classmethod
    def _optional_text_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("补充说明不能为空")
        return normalized


class VerdictCorrectionService:
    """Own the single transaction that corrects facts, memory and difficulty."""

    def __init__(self, persistence: LearningPersistence, clock: Clock) -> None:
        self._persistence = persistence
        self._clock = clock

    def apply(
        self,
        attempt_id: str,
        command: VerdictCorrectionCommand,
    ) -> AssessmentAttemptV1:
        facts = self._persistence.learning_facts.facts()
        attempts = project_assessment_attempts(facts)
        attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
        if attempt is None:
            raise AssessmentAttemptNotFound(attempt_id)

        event_id = derive_id(attempt.attempt_id, VERDICT_CORRECTED, command.request_id)
        existing_fact = next((fact for fact in facts if fact.event_id == event_id), None)
        if existing_fact is not None:
            if not self._matches(existing_fact.payload, command):
                raise VerdictCorrectionConflict("相同 request_id 已用于不同的判卷纠正")
            return next(
                item for item in project_assessment_attempts(facts) if item.attempt_id == attempt_id
            )

        previous = sorted(
            (
                fact
                for fact in facts
                if fact.event_type == VERDICT_CORRECTED
                and fact.payload.get("attempt_id") == attempt_id
            ),
            key=lambda fact: int(fact.payload.get("revision", 1)),
        )
        fact = verdict_correction_fact(
            attempt=attempt,
            request_id=command.request_id,
            final_verdict=command.final_verdict,
            reason=command.reason,
            source_event_ts=self._clock.now(),
            revision=len(previous) + 1,
            supersedes_id=None if not previous else previous[-1].event_id,
            supplemental_answer=command.supplemental_answer,
        )
        corrected_attempts = project_assessment_attempts([*facts, fact])
        memory_record, difficulty_progress = rebuild_learning_state(
            corrected_attempts,
            item_id=attempt.item_id,
        )
        fact = fact.model_copy(
            update={
                "payload": {
                    **fact.payload,
                    "reconciliation": {
                        "item_id": attempt.item_id,
                        "learning_memory_state": (
                            "not_in_memory" if memory_record is None else memory_record.state
                        ),
                        "difficulty_tier": int(difficulty_progress.tier),
                        "through_event_id": fact.event_id,
                    },
                }
            }
        )
        with self._persistence.transaction_owner.transaction():
            self._persistence.learning_facts.append(fact)
            self._persistence.memory.replace_record(attempt.item_id, memory_record)
            self._persistence.difficulty.replace_progress(
                attempt.item_id,
                difficulty_progress,
            )
        return next(
            item
            for item in project_assessment_attempts([*facts, fact])
            if item.attempt_id == attempt_id
        )

    @staticmethod
    def _matches(payload: Mapping[str, object], command: VerdictCorrectionCommand) -> bool:
        return (
            payload.get("final_verdict") == command.final_verdict
            and payload.get("reason") == command.reason
            and payload.get("supplemental_answer") == command.supplemental_answer
        )
