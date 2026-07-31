"""AssessmentPlan 是 CLI、Web Chat 与 FastAPI 共用的逐题编排 Interface。"""

import logging

import pytest

from grandquiz.domain.learning.assessment.plan import (
    MAX_ASSESSMENT_ROUNDS,
    AssessmentPlan,
    QuestionTypeSegment,
)


def test_mixed_segments_become_one_ordered_question_type_plan() -> None:
    plan = AssessmentPlan.create(
        rounds=99,
        question_type="追问",
        segments=[
            QuestionTypeSegment(count=2, question_type="选择题"),
            QuestionTypeSegment(count=1, question_type="简答题"),
        ],
    )

    assert plan.rounds == 3
    assert plan.question_type_intents == ("选择题", "选择题", "简答题")
    assert plan.intent_for(1) == "选择题"
    assert plan.intent_for(3) == "简答题"


def test_single_question_type_keeps_legacy_count_rules() -> None:
    assert AssessmentPlan.create(
        rounds=3,
        question_type="选择题",
    ).question_type_intents == ("选择题", "选择题", "选择题")
    assert AssessmentPlan.create(
        rounds=0,
        question_type=None,
    ).question_type_intents == (None,)
    assert (
        AssessmentPlan.create(
            rounds=MAX_ASSESSMENT_ROUNDS + 10,
            question_type="开放",
        ).rounds
        == MAX_ASSESSMENT_ROUNDS
    )


def test_nonpositive_segments_fall_back_without_creating_zero_round_plan() -> None:
    plan = AssessmentPlan.create(
        rounds=2,
        question_type="开放",
        segments=[
            QuestionTypeSegment(count=0, question_type="选择题"),
            QuestionTypeSegment(count=-1, question_type="简答题"),
        ],
    )

    assert plan.question_type_intents == ("开放", "开放")


def test_overlong_segment_plan_is_truncated_loudly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        plan = AssessmentPlan.create(
            rounds=1,
            question_type=None,
            segments=[
                QuestionTypeSegment(
                    count=MAX_ASSESSMENT_ROUNDS + 5,
                    question_type="选择题",
                )
            ],
        )

    assert plan.question_type_intents == ("选择题",) * MAX_ASSESSMENT_ROUNDS
    assert "截断" in caplog.text
