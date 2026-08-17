"""Human-owned promotion and rollback gates for Eval candidates."""

from __future__ import annotations

import pytest

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.question import ExpectedPoint, QuestionSpec
from grandquiz.domain.learning.eval_inbox import (
    DatasetSnapshotItemV1,
    DatasetSnapshotV1,
)
from grandquiz.domain.learning.grading_samples import GradingCalibrationSample
from grandquiz.evals.experiment import (
    EvalRunEvidence,
    EvalSampleEvidence,
    EvalSuiteInputs,
    PairedEvalExperiment,
    pair_subject_evaluations,
)
from grandquiz.evals.experiment_report import (
    EvalExperimentPolicy,
    build_experiment_report,
)
from grandquiz.evals.promotion import (
    HumanPromotionDecisionRequest,
    PromotionConflict,
    PromotionLedger,
    activate_candidate,
    evaluate_release_holdout,
    freeze_release_holdout,
    record_promotion_decision,
    rollback_subject,
)
from grandquiz.evals.proposal import (
    ApprovedFeedbackEvidence,
    ChangeProposalRequest,
    ProposalLedger,
    bind_proposal_experiment,
    propose_change,
)
from grandquiz.evals.subject import (
    EvalSubjectSnapshot,
    ProviderIdentity,
    SubjectEvaluation,
    snapshot_subject,
)


def _subject(prompt: str = "grading-open@v2") -> EvalSubjectSnapshot:
    return snapshot_subject(
        prompts={"grading_open": prompt},
        providers=(
            ProviderIdentity(
                role="basic",
                provider="openai-compatible",
                model="deepseek-chat",
                thinking="disabled",
            ),
        ),
        tool_schemas={"grade_answer": "sha256:tool-v2"},
        policies={"grading": "grading-policy@v2"},
    )


def _feedback() -> ApprovedFeedbackEvidence:
    return ApprovedFeedbackEvidence(
        feedback_id="f" * 64,
        source_kind="experiment_failure_slice",
        source_ids=("experiment-old",),
        source_hashes=("e" * 64,),
        surfaces=("answer_grading",),
        slice_ids=("equivalent-mechanism",),
        approval_request_id="approve-feedback-1",
        approval_reason="owner: confirmed development failure",
        approved_at=100.0,
        evidence_class="development_gold",
    )


def _proposal():
    baseline = _subject()
    _, proposal = propose_change(
        ProposalLedger(),
        baseline_subject=baseline,
        feedback=_feedback(),
        request=ChangeProposalRequest(
            request_id="proposal-request-1",
            target_surface="answer_grading",
            change_type="prompt",
            target_key="grading_open",
            expected_base_binding="grading-open@v2",
            candidate_version="grading-open@candidate-1",
            draft_content="识别等价机制与组合解释，同时保留证据约束。",
            rationale="修复已审批失败切片",
        ),
    )
    return baseline, proposal


def _sample(quality: float, *, verdict: VerdictLabel = "对") -> EvalSampleEvidence:
    return EvalSampleEvidence(
        sample_id="sample-1",
        surface="answer_grading",
        slice_id="equivalent-mechanism",
        execution_status="completed",
        rule_passed=verdict == "对",
        semantic_quality=quality,
        output_valid=True,
        execution_tokens=100,
        judge_tokens=20,
        latency_ms=100.0,
        retry_count=0,
        stability_rate=1.0,
    )


def _paired(
    baseline: EvalSubjectSnapshot,
    candidate: EvalSubjectSnapshot,
    *,
    dataset_id: str,
    dataset_hash: str,
    candidate_quality: float = 0.9,
) -> PairedEvalExperiment:
    suite = EvalSuiteInputs(
        dataset_snapshot_id=dataset_id,
        dataset_content_sha256=dataset_hash,
        suite_policy_version="grading-suite@v1",
        slice_manifest_version="grading-slices@v1",
        metric_versions=(("semantic_quality", "grading-quality@v1"),),
    )
    return pair_subject_evaluations(
        baseline=SubjectEvaluation(
            subject=baseline,
            report=EvalRunEvidence(suite=suite, samples=(_sample(0.6),)),
        ),
        candidate=SubjectEvaluation(
            subject=candidate,
            report=EvalRunEvidence(
                suite=suite,
                samples=(_sample(candidate_quality),),
            ),
        ),
    )


def _policy(policy_id: str = "grading-promotion@v1") -> EvalExperimentPolicy:
    return EvalExperimentPolicy(
        policy_id=policy_id,
        blocking_slice_ids=("equivalent-mechanism",),
        min_semantic_gain=0.1,
        max_execution_token_ratio=1.1,
        max_judge_token_ratio=1.1,
        max_latency_ratio=1.1,
        max_retry_increase=0,
        min_stability_rate=0.95,
    )


def _development_evidence():
    baseline, proposal = _proposal()
    experiment = _paired(
        baseline,
        proposal.candidate_subject,
        dataset_id="development-gold-1",
        dataset_hash="d" * 64,
    )
    evaluation = bind_proposal_experiment(proposal, experiment)
    report = build_experiment_report(experiment, policy=_policy("development-policy@v1"))
    return baseline, proposal, evaluation, report


def _blind_sample() -> GradingCalibrationSample:
    question = QuestionSpec(
        question="HTTP/1.0 默认如何处理连接？",
        expected_points=[
            ExpectedPoint(
                point_id="close",
                description="默认关闭",
                cited_evidence="HTTP/1.0 默认在响应后关闭连接。",
            )
        ],
        reference_answer="默认关闭连接。",
        cited_evidence=["HTTP/1.0 默认在响应后关闭连接。"],
    )
    return GradingCalibrationSample(
        sample_id="holdout-blind-1",
        annotator="owner",
        blind_to_model_output=True,
        question=question,
        learner_answer="响应后默认关闭。",
        human_verdict="对",
        human_matched_points=["close"],
        human_missing_points=[],
    )


def _snapshot(
    *,
    eligible: bool = True,
    snapshot_id: str = "release-holdout-1",
    content_sha256: str = "c" * 64,
) -> DatasetSnapshotV1:
    sample = _blind_sample()
    item = DatasetSnapshotItemV1(
        candidate_id="holdout-candidate-1",
        source_kind="blind_grading_label",
        payload_schema_version=sample.schema_version,
        payload_hash="b" * 64,
        payload=sample,
        release_gate_eligible=eligible,
        review_request_id="privacy-review-1",
        review_reason="owner: no private content",
        reviewed_at=200.0,
    )
    return DatasetSnapshotV1(
        snapshot_id=snapshot_id,
        content_sha256=content_sha256,
        candidate_count=1,
        eligible_blind_count=1 if eligible else 0,
        exploratory_count=0 if eligible else 1,
        items=(item,),
        created_at=210.0,
    )


def _accept_request() -> HumanPromotionDecisionRequest:
    return HumanPromotionDecisionRequest(
        request_id="promotion-decision-1",
        decision="accept",
        actor="owner",
        reason="paired Development Eval is eligible for holdout",
        decided_at=300.0,
    )


def _eligible_decision():
    baseline, proposal, evaluation, report = _development_evidence()
    ledger, decision = record_promotion_decision(
        PromotionLedger(active_subject_id=baseline.subject_id),
        proposal=proposal,
        evaluation=evaluation,
        report=report,
        request=_accept_request(),
    )
    return baseline, proposal, evaluation, report, ledger, decision


def test_development_gold_accept_only_becomes_eligible_for_holdout() -> None:
    baseline, proposal, evaluation, report = _development_evidence()

    ledger, decision = record_promotion_decision(
        PromotionLedger(active_subject_id=baseline.subject_id),
        proposal=proposal,
        evaluation=evaluation,
        report=report,
        request=_accept_request(),
    )

    assert decision.decision == "accept"
    assert decision.resulting_state == "eligible_for_holdout"
    assert decision.report_id == report.experiment_id
    assert ledger.active_subject_id == baseline.subject_id
    assert ledger.selections == ()


def test_noneligible_development_report_cannot_be_accepted() -> None:
    baseline, proposal = _proposal()
    experiment = _paired(
        baseline,
        proposal.candidate_subject,
        dataset_id="development-gold-1",
        dataset_hash="d" * 64,
        candidate_quality=0.4,
    )
    evaluation = bind_proposal_experiment(proposal, experiment)
    report = build_experiment_report(experiment, policy=_policy("development-policy@v1"))

    with pytest.raises(PromotionConflict, match="eligible for human review"):
        record_promotion_decision(
            PromotionLedger(active_subject_id=baseline.subject_id),
            proposal=proposal,
            evaluation=evaluation,
            report=report,
            request=_accept_request(),
        )


@pytest.mark.parametrize(
    ("decision_name", "expected_state"),
    (("reject", "rejected"), ("keep_experimental", "experimental")),
)
def test_human_can_reject_or_keep_candidate_experimental(
    decision_name: str,
    expected_state: str,
) -> None:
    baseline, proposal, evaluation, report = _development_evidence()
    _, decision = record_promotion_decision(
        PromotionLedger(active_subject_id=baseline.subject_id),
        proposal=proposal,
        evaluation=evaluation,
        report=report,
        request=_accept_request().model_copy(update={"decision": decision_name}),
    )

    assert decision.resulting_state == expected_state


def test_release_holdout_must_be_frozen_private_approved_eligible_and_unseen() -> None:
    with pytest.raises(PromotionConflict, match="eligible blind"):
        freeze_release_holdout(
            _snapshot(eligible=False),
            request_id="freeze-holdout-1",
            threshold_policy_id="release-thresholds@v1",
            unseen_confirmed=True,
        )

    with pytest.raises(PromotionConflict, match="unseen"):
        freeze_release_holdout(
            _snapshot(),
            request_id="freeze-holdout-1",
            threshold_policy_id="release-thresholds@v1",
            unseen_confirmed=False,
        )


def test_revealed_holdout_becomes_development_gold_regardless_of_result() -> None:
    baseline, proposal, development, _, _, decision = _eligible_decision()
    holdout = freeze_release_holdout(
        _snapshot(),
        request_id="freeze-holdout-1",
        threshold_policy_id="release-thresholds@v1",
        unseen_confirmed=True,
    )
    failed_experiment = _paired(
        baseline,
        proposal.candidate_subject,
        dataset_id=holdout.dataset_snapshot_id,
        dataset_hash=holdout.dataset_content_sha256,
        candidate_quality=0.4,
    )
    failed_report = build_experiment_report(
        failed_experiment,
        policy=_policy(holdout.threshold_policy_id),
    )

    result = evaluate_release_holdout(
        holdout,
        proposal=proposal,
        development_evaluation=development,
        decision=decision,
        experiment=failed_experiment,
        report=failed_report,
        revealed_at=400.0,
    )

    assert result.passed is False
    assert result.evidence_class_after_reveal == "development_gold"
    assert result.release_holdout_eligible is False


def test_release_holdout_cannot_reuse_the_development_snapshot() -> None:
    baseline, proposal, development, _, _, decision = _eligible_decision()
    holdout = freeze_release_holdout(
        _snapshot(snapshot_id="development-gold-1", content_sha256="d" * 64),
        request_id="freeze-holdout-1",
        threshold_policy_id="release-thresholds@v1",
        unseen_confirmed=True,
    )
    experiment = _paired(
        baseline,
        proposal.candidate_subject,
        dataset_id=holdout.dataset_snapshot_id,
        dataset_hash=holdout.dataset_content_sha256,
    )

    with pytest.raises(PromotionConflict, match="new and distinct"):
        evaluate_release_holdout(
            holdout,
            proposal=proposal,
            development_evaluation=development,
            decision=decision,
            experiment=experiment,
            report=build_experiment_report(
                experiment,
                policy=_policy(holdout.threshold_policy_id),
            ),
            revealed_at=400.0,
        )


def test_activation_requires_explicit_decision_and_passed_new_holdout() -> None:
    baseline, proposal, development, _, ledger, decision = _eligible_decision()
    holdout = freeze_release_holdout(
        _snapshot(),
        request_id="freeze-holdout-1",
        threshold_policy_id="release-thresholds@v1",
        unseen_confirmed=True,
    )
    experiment = _paired(
        baseline,
        proposal.candidate_subject,
        dataset_id=holdout.dataset_snapshot_id,
        dataset_hash=holdout.dataset_content_sha256,
    )
    report = build_experiment_report(experiment, policy=_policy(holdout.threshold_policy_id))
    result = evaluate_release_holdout(
        holdout,
        proposal=proposal,
        development_evaluation=development,
        decision=decision,
        experiment=experiment,
        report=report,
        revealed_at=400.0,
    )

    with pytest.raises(PromotionConflict, match="human decision"):
        activate_candidate(
            PromotionLedger(active_subject_id=baseline.subject_id),
            decision_id=decision.decision_id,
            proposal=proposal,
            holdout=result,
            request_id="activate-1",
            actor="owner",
            reason="release holdout passed",
            selected_at=500.0,
        )

    activated, selection = activate_candidate(
        ledger,
        decision_id=decision.decision_id,
        proposal=proposal,
        holdout=result,
        request_id="activate-1",
        actor="owner",
        reason="release holdout passed",
        selected_at=500.0,
    )
    replayed, replayed_selection = activate_candidate(
        activated,
        decision_id=decision.decision_id,
        proposal=proposal,
        holdout=result,
        request_id="activate-1",
        actor="owner",
        reason="release holdout passed",
        selected_at=500.0,
    )

    assert activated.active_subject_id == proposal.candidate_subject.subject_id
    assert replayed == activated
    assert replayed_selection == selection
    assert selection.previous_subject_id == baseline.subject_id
    assert selection.selected_subject_id == proposal.candidate_subject.subject_id
    assert selection.kind == "activation"


def test_rollback_selects_previous_identity_without_editing_history() -> None:
    baseline, proposal, development, _, ledger, decision = _eligible_decision()
    holdout = freeze_release_holdout(
        _snapshot(),
        request_id="freeze-holdout-1",
        threshold_policy_id="release-thresholds@v1",
        unseen_confirmed=True,
    )
    experiment = _paired(
        baseline,
        proposal.candidate_subject,
        dataset_id=holdout.dataset_snapshot_id,
        dataset_hash=holdout.dataset_content_sha256,
    )
    result = evaluate_release_holdout(
        holdout,
        proposal=proposal,
        development_evaluation=development,
        decision=decision,
        experiment=experiment,
        report=build_experiment_report(
            experiment,
            policy=_policy(holdout.threshold_policy_id),
        ),
        revealed_at=400.0,
    )
    activated, activation = activate_candidate(
        ledger,
        decision_id=decision.decision_id,
        proposal=proposal,
        holdout=result,
        request_id="activate-1",
        actor="owner",
        reason="release holdout passed",
        selected_at=500.0,
    )
    original_decisions = activated.decisions

    rolled_back, rollback = rollback_subject(
        activated,
        activation_id=activation.selection_id,
        request_id="rollback-1",
        actor="owner",
        reason="production regression",
        selected_at=600.0,
    )
    replayed, replayed_rollback = rollback_subject(
        rolled_back,
        activation_id=activation.selection_id,
        request_id="rollback-1",
        actor="owner",
        reason="production regression",
        selected_at=600.0,
    )

    assert rolled_back.active_subject_id == baseline.subject_id
    assert replayed == rolled_back
    assert replayed_rollback == rollback
    assert rolled_back.decisions == original_decisions
    assert rolled_back.selections == (activation, rollback)
    assert rollback.kind == "rollback"
    assert rollback.previous_subject_id == proposal.candidate_subject.subject_id
    assert rollback.selected_subject_id == baseline.subject_id


def test_safe_promotion_ledger_omits_prompt_and_private_sample_bodies() -> None:
    _, _, _, _, ledger, _ = _eligible_decision()

    serialized = ledger.model_dump_json()

    assert "识别等价机制" not in serialized
    assert "HTTP/1.0" not in serialized
    assert "sk-" not in serialized
