from pathlib import Path

import pytest

from grandquiz.domain.learning.assessment_history import (
    AssessmentAttemptV1,
    demand_validation_fact,
    project_demand_validations,
)
from grandquiz.domain.learning.learning_facts import (
    LearningFactEnvelope,
    SqliteLearningFactJournal,
)
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.domain.learning.state import LearningStateWriter
from grandquiz.interfaces.learning_outbox import publish_pending_learning_facts
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.trace import TraceStore


def _fact() -> LearningFactEnvelope:
    return LearningFactEnvelope(
        event_id="fact-atomic-1",
        event_type="learning.assessment_judgement_committed",
        entity_id="attempt-atomic-1",
        trace_id="trace-atomic-1",
        source_event_seq=4,
        source_event_ts=5.0,
        payload_schema_version="assessment-judgement-committed.v1",
        payload={"attempt_id": "attempt-atomic-1"},
    )


def _attempt() -> AssessmentAttemptV1:
    return AssessmentAttemptV1.model_validate(
        {
            "taxonomy_version": "vocabulary.v1",
            "attempt_id": "attempt-demand-1",
            "trace_id": "trace-demand-1",
            "assessment_span_id": "assessment-span-1",
            "item_id": "item-demand-1",
            "question_text": "为什么事件是一条脊柱？",
            "answer_text": "因为多个消费者共享同一事件流。",
            "initial_verdict": "对",
            "final_verdict": "对",
            "adaptive_route": {"format": "open_response", "strategy": "standard"},
            "effective_route": {"format": "open_response", "strategy": "standard"},
            "routing_source": "adaptive",
            "input_modality": "text",
            "answer_format": "natural_language",
            "evidence_revealed_before_answer": False,
            "elapsed_ms": 1_000,
            "question_generation": {"kind": "model", "version": "question-open@v1"},
            "grading": {"kind": "model", "version": "grade-answer@v1"},
            "source_event_cursor": {"first_seq": 1, "last_seq": 2},
        }
    )


class _FailingJournal(SqliteLearningFactJournal):
    def append(self, fact: LearningFactEnvelope) -> None:
        super().append(fact)
        raise RuntimeError("journal write failed")


def test_journal_failure_rolls_back_all_operational_ledgers(tmp_path: Path) -> None:
    db_path = tmp_path / "learning.db"
    with LearningPersistence(db_path) as persistence:
        resource = LearningResource(resource_id="resource-1", url="file://local/test")
        item = KnowledgeItem(
            item_id="item-1",
            resource_id=resource.resource_id,
            concept="PageIndex",
            summary="一种长文档检索方法",
            evidence=[Evidence(quote="PageIndex 以树结构组织长文档")],
            confidence=0.9,
        )
        persistence.store.replace_snapshot(resource, [item])
        journal = _FailingJournal(persistence.transaction_owner)

        with pytest.raises(RuntimeError, match="journal write failed"):
            LearningStateWriter(
                memory=persistence.memory,
                asked_questions=persistence.asked_questions,
                difficulty=persistence.difficulty,
                learning_facts=journal,
            ).commit_judgement(
                item_id=item.item_id,
                question="PageIndex 是什么？",
                verdict="错",
                elapsed_ms=1000,
                learning_fact=_fact(),
            )

        assert persistence.memory.state_of(item.item_id) is None
        assert persistence.asked_questions.asked_before(item.item_id) == []
        assert persistence.learning_facts.facts() == []


def test_pending_outbox_republishes_once_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "learning.db"
    trace_path = tmp_path / "trace.db"
    with LearningPersistence(db_path) as persistence:
        persistence.learning_facts.append(_fact())
        trace = TraceStore(trace_path)
        try:
            assert (
                publish_pending_learning_facts(
                    persistence.learning_facts,
                    trace,
                    clock=ManualClock(start=10.0),
                )
                == 1
            )
            assert (
                publish_pending_learning_facts(
                    persistence.learning_facts,
                    trace,
                    clock=ManualClock(start=20.0),
                )
                == 0
            )
            events = trace.events("trace-atomic-1")
        finally:
            trace.close()

    assert len(events) == 1
    assert events[0].payload["event_id"] == "fact-atomic-1"


def test_proposed_demand_does_not_supersede_last_approved_validation() -> None:
    attempt = _attempt()
    approved_fact, approved = demand_validation_fact(
        attempt=attempt,
        request_id="manual-validation",
        validated_demand="explain",
        validator_kind="user",
        validator_version="manual.v1",
        calibration_version=None,
        rationale="人工确认题目要求解释因果",
        source_event_ts=1.0,
    )
    proposed_fact, proposed = demand_validation_fact(
        attempt=attempt,
        request_id="uncalibrated-judge",
        validated_demand="apply",
        validator_kind="calibrated_judge",
        validator_version="demand-judge.v1",
        calibration_version=None,
        rationale="尚未通过校准的模型建议",
        source_event_ts=2.0,
        previous=[approved],
    )

    projected = project_demand_validations([approved_fact, proposed_fact])
    active_approved = [
        validation
        for validation in projected
        if validation.review_status == "approved" and validation.lifecycle_status == "active"
    ]

    assert proposed.review_status == "proposed"
    assert [validation.validation_id for validation in active_approved] == [approved.validation_id]
