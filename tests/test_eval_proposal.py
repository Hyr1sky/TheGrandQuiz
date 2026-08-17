"""Human-approved feedback can only create bounded Eval candidates."""

from __future__ import annotations

import pytest

from grandquiz.domain.learning.eval_candidates import GradingEvalCandidateV1
from grandquiz.domain.learning.eval_inbox import EvalInboxCandidateV1
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
from grandquiz.evals.proposal import (
    ChangeProposalRequest,
    ProposalConflict,
    ProposalLedger,
    approved_feedback_from_failure_slices,
    approved_feedback_from_inbox,
    bind_proposal_experiment,
    propose_change,
)
from grandquiz.evals.subject import (
    EvalSubjectSnapshot,
    ProviderIdentity,
    SubjectEvaluation,
    snapshot_subject,
)


def _correction_payload() -> GradingEvalCandidateV1:
    return GradingEvalCandidateV1(
        candidate_id="correction-1",
        attempt_id="attempt-1",
        item_id="item-1",
        source_trace_id="trace-assessment",
        correction_trace_id="trace-correction",
        question_text="HTTP/1.0 默认如何处理连接？",
        answer_text="响应后关闭，也可协商 Keep-Alive。",
        question_format="open_response",
        grading_version="answer-grade.v2",
        model_verdict="错",
        human_verdict="对",
        correction_reason="覆盖了两个评分点",
        label_kind="overturned",
    )


def _inbox_candidate(*, review_status: str = "approved") -> EvalInboxCandidateV1:
    payload = _correction_payload()
    return EvalInboxCandidateV1.model_validate(
        {
            "candidate_id": "inbox-1",
            "source_kind": "verdict_correction",
            "dedupe_key": "attempt-1",
            "source_request_id": "correction:correction-1",
            "payload_schema_version": payload.schema_version,
            "payload_hash": "a" * 64,
            "payload": payload,
            "lifecycle_status": "active",
            "review_status": review_status,
            "release_gate_eligible": False,
            "privacy_review_required": True,
            "review_request_id": "review-1" if review_status == "approved" else None,
            "review_reason": "本地隐私审查通过" if review_status == "approved" else None,
            "reviewed_at": 100.0 if review_status == "approved" else None,
            "created_at": 90.0,
        }
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


def _request(
    request_id: str = "proposal-request-1",
    *,
    version: str = "grading-open@candidate-1",
    content: str = "识别等价机制与组合解释，同时保留证据约束。",
    supersedes: str | None = None,
) -> ChangeProposalRequest:
    return ChangeProposalRequest(
        request_id=request_id,
        target_surface="answer_grading",
        change_type="prompt",
        target_key="grading_open",
        expected_base_binding="grading-open@v2",
        candidate_version=version,
        draft_content=content,
        rationale="修复已审批纠正暴露的语义召回缺口",
        supersedes_proposal_id=supersedes,
    )


def _sample(quality: float) -> EvalSampleEvidence:
    return EvalSampleEvidence(
        sample_id="sample-1",
        surface="answer_grading",
        slice_id="equivalent-mechanism",
        execution_status="completed",
        rule_passed=True,
        semantic_quality=quality,
        output_valid=True,
        execution_tokens=100,
        judge_tokens=20,
        latency_ms=100.0,
        retry_count=0,
        stability_rate=1.0,
    )


def _paired(
    baseline_subject: EvalSubjectSnapshot,
    candidate_subject: EvalSubjectSnapshot,
) -> PairedEvalExperiment:
    suite = EvalSuiteInputs(
        dataset_snapshot_id="development-gold-1",
        dataset_content_sha256="d" * 64,
        suite_policy_version="grading-suite@v1",
        slice_manifest_version="grading-slices@v1",
        metric_versions=(("semantic_quality", "grading-quality@v1"),),
    )
    return pair_subject_evaluations(
        baseline=SubjectEvaluation(
            subject=baseline_subject,
            report=EvalRunEvidence(suite=suite, samples=(_sample(0.6),)),
        ),
        candidate=SubjectEvaluation(
            subject=candidate_subject,
            report=EvalRunEvidence(suite=suite, samples=(_sample(0.9),)),
        ),
    )


def test_approved_verdict_correction_creates_exploratory_bounded_subject() -> None:
    baseline = _subject()
    feedback = approved_feedback_from_inbox(_inbox_candidate())

    ledger, proposal = propose_change(
        ProposalLedger(),
        baseline_subject=baseline,
        feedback=feedback,
        request=_request(),
    )

    assert feedback.evidence_class == "exploratory"
    assert feedback.release_holdout_eligible is False
    assert proposal.baseline_subject_id == baseline.subject_id
    assert proposal.target_surface == "answer_grading"
    assert proposal.content_sha256 in dict(proposal.candidate_subject.prompts)["grading_open"]
    assert dict(proposal.candidate_subject.policies) == dict(baseline.policies)
    assert proposal.candidate_subject.providers == baseline.providers
    assert proposal.candidate_subject.tool_schemas == baseline.tool_schemas
    assert ledger.active() == (proposal,)


def test_pending_feedback_and_unapproved_failure_slice_fail_closed() -> None:
    with pytest.raises(ProposalConflict, match="approved active"):
        approved_feedback_from_inbox(_inbox_candidate(review_status="pending"))

    paired = _paired(_subject(), _subject("grading-open@candidate"))
    report = build_experiment_report(
        paired,
        policy=EvalExperimentPolicy(
            policy_id="grading-comparison@v1",
            blocking_slice_ids=("equivalent-mechanism",),
            min_semantic_gain=0.1,
            max_execution_token_ratio=1.1,
            max_judge_token_ratio=1.1,
            max_latency_ratio=1.1,
            max_retry_increase=0,
            min_stability_rate=0.95,
        ),
    )
    with pytest.raises(ValueError, match="approval"):
        approved_feedback_from_failure_slices(
            report,
            slice_ids=("equivalent-mechanism",),
            approval_request_id="",
            reviewer="owner",
            reason="confirmed failure",
        )


def test_approved_failure_slice_can_propose_one_policy_binding() -> None:
    baseline = _subject()
    report = build_experiment_report(
        _paired(baseline, _subject("grading-open@candidate")),
        policy=EvalExperimentPolicy(
            policy_id="grading-comparison@v1",
            blocking_slice_ids=("equivalent-mechanism",),
            min_semantic_gain=0.1,
            max_execution_token_ratio=1.1,
            max_judge_token_ratio=1.1,
            max_latency_ratio=1.1,
            max_retry_increase=0,
            min_stability_rate=0.95,
        ),
    )
    feedback = approved_feedback_from_failure_slices(
        report,
        slice_ids=("equivalent-mechanism",),
        approval_request_id="approve-slice-1",
        reviewer="owner",
        reason="confirmed development failure",
    )
    request = ChangeProposalRequest(
        request_id="policy-proposal-1",
        target_surface="answer_grading",
        change_type="policy",
        target_key="grading",
        expected_base_binding="grading-policy@v2",
        candidate_version="grading-policy@candidate-1",
        draft_content="等价机制切片使用组合声明核验策略。",
        rationale="阻断切片需要独立修复",
    )

    _, proposal = propose_change(
        ProposalLedger(),
        baseline_subject=baseline,
        feedback=feedback,
        request=request,
    )

    assert feedback.evidence_class == "development_gold"
    assert feedback.slice_ids == ("equivalent-mechanism",)
    assert dict(proposal.candidate_subject.prompts) == dict(baseline.prompts)
    assert proposal.content_sha256 in dict(proposal.candidate_subject.policies)["grading"]


def test_proposal_rejects_wrong_target_version_and_secret_bearing_draft() -> None:
    feedback = approved_feedback_from_inbox(_inbox_candidate())
    with pytest.raises(ProposalConflict, match="base binding"):
        propose_change(
            ProposalLedger(),
            baseline_subject=_subject(),
            feedback=feedback,
            request=_request().model_copy(update={"expected_base_binding": "grading-open@stale"}),
        )

    with pytest.raises(ValueError, match="secret"):
        propose_change(
            ProposalLedger(),
            baseline_subject=_subject(),
            feedback=feedback,
            request=_request(content="Authorization: Bearer private-token"),
        )


def test_duplicate_request_is_idempotent_and_superseding_preserves_history() -> None:
    baseline = _subject()
    feedback = approved_feedback_from_inbox(_inbox_candidate())
    first_ledger, first = propose_change(
        ProposalLedger(),
        baseline_subject=baseline,
        feedback=feedback,
        request=_request(),
    )

    replayed_ledger, replayed = propose_change(
        first_ledger,
        baseline_subject=baseline,
        feedback=feedback,
        request=_request(),
    )
    assert replayed == first
    assert replayed_ledger == first_ledger

    second_ledger, second = propose_change(
        first_ledger,
        baseline_subject=baseline,
        feedback=feedback,
        request=_request(
            "proposal-request-2",
            version="grading-open@candidate-2",
            content="优先判断机制等价性，再逐项核验关键声明。",
            supersedes=first.proposal_id,
        ),
    )
    assert second.supersedes_proposal_id == first.proposal_id
    assert second_ledger.proposals == (first, second)
    assert second_ledger.active() == (second,)

    with pytest.raises(ProposalConflict, match="idempotency"):
        propose_change(
            first_ledger,
            baseline_subject=baseline,
            feedback=feedback,
            request=_request(content="same request, different content"),
        )


def test_proposal_can_only_bind_the_exact_paired_experiment() -> None:
    baseline = _subject()
    feedback = approved_feedback_from_inbox(_inbox_candidate())
    _, proposal = propose_change(
        ProposalLedger(),
        baseline_subject=baseline,
        feedback=feedback,
        request=_request(),
    )

    evaluation = bind_proposal_experiment(
        proposal,
        _paired(baseline, proposal.candidate_subject),
    )
    assert evaluation.proposal_id == proposal.proposal_id
    assert evaluation.baseline_subject_id == baseline.subject_id
    assert evaluation.candidate_subject_id == proposal.candidate_subject.subject_id

    wrong = _paired(baseline, _subject("grading-open@wrong-candidate"))
    with pytest.raises(ProposalConflict, match="candidate subject"):
        bind_proposal_experiment(proposal, wrong)
