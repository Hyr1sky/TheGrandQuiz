import pytest

from grandquiz.domain.learning.assessment.clarification import (
    ClarificationFlow,
    ClarificationStateError,
    plan_clarification,
)
from grandquiz.domain.learning.assessment.grading import (
    OpenAnswerDiagnosisKind,
    PointAssessment,
    Verdict,
    VerdictLabel,
)
from grandquiz.domain.learning.assessment.question import ExpectedPoint, QuestionSpec

_EVIDENCE = "材料明确说明状态应被保存并在新会话恢复。"


def _question(*, critical: list[str] | None = None) -> QuestionSpec:
    return QuestionSpec(
        question="如何让任务在新会话继续？",
        expected_points=[
            ExpectedPoint(
                point_id="state_snapshot",
                description="保存结构化状态快照",
                cited_evidence=_EVIDENCE,
            ),
            ExpectedPoint(
                point_id="fresh_session",
                description="在新的会话中恢复并继续",
                cited_evidence=_EVIDENCE,
            ),
            ExpectedPoint(
                point_id="retest",
                description="恢复后重新验证关键假设",
                cited_evidence=_EVIDENCE,
            ),
        ],
        critical_point_ids=critical or [],
        reference_answer="保存状态，在新会话恢复，并重新验证关键假设。",
        cited_evidence=[_EVIDENCE],
    )


def _verdict(
    *,
    matched: list[str],
    missing: list[str],
    verdict: VerdictLabel,
    diagnosis: OpenAnswerDiagnosisKind = "uncertain",
) -> Verdict:
    assessments = [
        PointAssessment(
            point_id=point_id,
            label="matched",
            answer_evidence_ids=["v1e000_004"],
            reason="回答中有直接表达。",
        )
        for point_id in matched
    ] + [
        PointAssessment(
            point_id=point_id,
            label="missing",
            reason="无法确定这段表达是否覆盖该点。",
        )
        for point_id in missing
    ]
    return Verdict(
        verdict=verdict,
        matched_points=matched,
        missing_points=missing,
        point_assessments=assessments,
        diagnosis=diagnosis,
        reason="需要学习者澄清一个关键点。",
        cited_evidence=[_EVIDENCE],
    )


def test_uncertain_decisive_missing_point_opens_one_clarification() -> None:
    question = _question()
    verdict = _verdict(
        matched=["state_snapshot", "fresh_session"],
        missing=["retest"],
        verdict="勉强",
    )

    request = plan_clarification(question, verdict)

    assert request is not None
    assert request.point_id == "retest"
    assert request.initial_verdict == "勉强"
    assert request.verdict_if_matched == "对"
    assert request.prompt == "你能再明确说明“恢复后重新验证关键假设”吗？请只补充这一点。"


def test_planner_requires_uncertainty_and_a_single_point_that_changes_verdict() -> None:
    question = _question(critical=["fresh_session", "retest"])
    certain = _verdict(
        matched=["state_snapshot"],
        missing=["fresh_session", "retest"],
        verdict="错",
        diagnosis="missing_key_point",
    )
    two_critical_missing = certain.model_copy(update={"diagnosis": "uncertain"})

    assert plan_clarification(question, certain) is None
    assert plan_clarification(question, two_critical_missing) is None


def test_planner_prioritizes_a_decisive_critical_point() -> None:
    question = _question(critical=["fresh_session"])
    verdict = _verdict(
        matched=["state_snapshot"],
        missing=["fresh_session", "retest"],
        verdict="错",
    )

    request = plan_clarification(question, verdict)

    assert request is not None
    assert request.point_id == "fresh_session"
    assert request.verdict_if_matched == "勉强"


def test_clarification_flow_accepts_one_supplement_and_then_stops() -> None:
    question = _question()
    initial = _verdict(
        matched=["state_snapshot", "fresh_session"],
        missing=["retest"],
        verdict="勉强",
    )
    request = plan_clarification(question, initial)
    assert request is not None
    flow = ClarificationFlow.start(initial_answer="我会保存状态并在新会话恢复。", request=request)

    ready = flow.submit("恢复后还要重新验证原来的关键假设。")

    assert ready.phase == "ready_to_regrade"
    assert ready.answer_for_regrade == (
        "首次回答：\n我会保存状态并在新会话恢复。\n\n"
        "针对评分点“恢复后重新验证关键假设”的补充：\n"
        "恢复后还要重新验证原来的关键假设。"
    )
    with pytest.raises(ClarificationStateError):
        ready.submit("不能再补充第二次。")

    still_uncertain = _verdict(
        matched=["state_snapshot", "fresh_session"],
        missing=["retest"],
        verdict="勉强",
    )
    stopped = ready.finish(still_uncertain)
    assert stopped.phase == "needs_review"
    with pytest.raises(ClarificationStateError):
        stopped.finish(still_uncertain)


def test_clarification_flow_rejects_blank_supplement() -> None:
    question = _question()
    initial = _verdict(
        matched=["state_snapshot", "fresh_session"],
        missing=["retest"],
        verdict="勉强",
    )
    request = plan_clarification(question, initial)
    assert request is not None
    flow = ClarificationFlow.start(initial_answer="初答", request=request)

    with pytest.raises(ValueError, match="答案不能为空"):
        flow.submit("   ")


def test_clarification_flow_resolves_when_regrade_is_determinate() -> None:
    question = _question()
    initial = _verdict(
        matched=["state_snapshot", "fresh_session"],
        missing=["retest"],
        verdict="勉强",
    )
    request = plan_clarification(question, initial)
    assert request is not None
    ready = ClarificationFlow.start(initial_answer="初答", request=request).submit("补答")
    final = _verdict(
        matched=["state_snapshot", "fresh_session", "retest"],
        missing=[],
        verdict="对",
        diagnosis="complete",
    )

    resolved = ready.finish(final)

    assert resolved.phase == "resolved"
    assert resolved.final_verdict == final
