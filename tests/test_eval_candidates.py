"""Verdict corrections become local, review-required Eval candidates."""

from grandquiz.domain.learning.assessment_history import (
    project_assessment_attempts,
    verdict_correction_fact,
)
from grandquiz.domain.learning.eval_candidates import project_grading_eval_candidates
from grandquiz.domain.learning.learning_facts import LearningFactEnvelope


def _assessment_fact() -> LearningFactEnvelope:
    return LearningFactEnvelope(
        event_id="judgement-1",
        event_type="learning.assessment_judgement_committed",
        entity_id="attempt-1",
        trace_id="trace-1",
        source_event_seq=7,
        source_event_ts=10.0,
        payload_schema_version="assessment-judgement-committed.v1",
        payload={
            "attempt_id": "attempt-1",
            "assessment_span_id": "assessment-1",
            "question_event_seq": 2,
            "item_id": "item-http10",
            "question_text": "HTTP/1.0 默认如何处理连接？",
            "answer_text": "请求响应后关闭，也可以协商 Keep-Alive。",
            "initial_verdict": "错",
            "concept_state": "薄弱",
            "difficulty_tier": 3,
            "adaptive_route": {"format": "open_response", "strategy": "standard"},
            "effective_route": {"format": "open_response", "strategy": "standard"},
            "routing_source": "adaptive",
            "input_modality": "text",
            "answer_format": "natural_language",
            "evidence_revealed_before_answer": False,
            "elapsed_ms": 1500,
            "question_generation": {"kind": "model", "version": "question@v1"},
            "grading": {"kind": "model", "version": "answer-grade@v1"},
        },
    )


def test_latest_correction_projects_one_current_non_blind_candidate() -> None:
    base = _assessment_fact()
    attempt = project_assessment_attempts([base])[0]
    first = verdict_correction_fact(
        attempt=attempt,
        request_id="correction-1",
        final_verdict="勉强",
        reason="默认关闭这一点答到了。",
        source_event_ts=11.0,
    )
    after_first = project_assessment_attempts([base, first])[0]
    second = verdict_correction_fact(
        attempt=after_first,
        request_id="correction-2",
        final_verdict="对",
        reason="两个评分点都已覆盖。",
        source_event_ts=12.0,
        revision=2,
        supersedes_id=first.event_id,
    )

    candidates = project_grading_eval_candidates([base, first, second])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id == second.event_id
    assert candidate.attempt_id == "attempt-1"
    assert candidate.model_verdict == "错"
    assert candidate.human_verdict == "对"
    assert candidate.correction_reason == "两个评分点都已覆盖。"
    assert candidate.label_kind == "overturned"
    assert candidate.blind_to_model_output is False
    assert candidate.release_gate_eligible is False
    assert candidate.privacy_review_required is True
    assert candidate.question_text == "HTTP/1.0 默认如何处理连接？"
    assert candidate.answer_text == "请求响应后关闭，也可以协商 Keep-Alive。"


def test_attempt_without_correction_is_not_an_eval_candidate() -> None:
    assert project_grading_eval_candidates([_assessment_fact()]) == []
