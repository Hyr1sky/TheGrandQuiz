"""Human-owned promotion decisions, Release Holdout gates, and rollback identity.

The module is intentionally an append-only selector of immutable Eval Subject ids. It cannot
edit prompt files, Provider bindings, cassettes, datasets, or learning facts.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grandquiz.domain.learning.eval_inbox import DatasetSnapshotV1
from grandquiz.evals.experiment import PairedEvalExperiment
from grandquiz.evals.experiment_report import EvalExperimentReport
from grandquiz.evals.proposal import EvalChangeProposal, ProposalEvaluation

PromotionDecisionName = Literal["accept", "reject", "keep_experimental"]
PromotionCandidateState = Literal["eligible_for_holdout", "rejected", "experimental"]
SubjectSelectionKind = Literal["activation", "rollback"]


class PromotionConflict(ValueError):
    """A decision, holdout, activation, or rollback bypassed a required gate."""


class HumanPromotionDecisionRequest(BaseModel):
    """Explicit human intent; an Eval report alone can never construct this artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1)
    decision: PromotionDecisionName
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decided_at: float

    @field_validator("request_id", "actor", "reason")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("promotion decision fields must not be blank")
        return normalized


class HumanPromotionDecision(BaseModel):
    """Safe immutable decision identity; report/sample bodies are deliberately absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-promotion-decision.v1"] = "eval-promotion-decision.v1"
    decision_id: str = Field(min_length=64, max_length=64)
    request_id: str
    proposal_id: str
    report_id: str
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_subject_id: str
    candidate_subject_id: str
    decision: PromotionDecisionName
    resulting_state: PromotionCandidateState
    actor: str
    reason_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decided_at: float
    development_evidence_only: Literal[True] = True


class ReleaseHoldoutManifest(BaseModel):
    """Pre-reveal identity for one frozen, privacy-approved, eligible blind snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-release-holdout.v1"] = "eval-release-holdout.v1"
    holdout_id: str = Field(min_length=64, max_length=64)
    request_id: str
    dataset_snapshot_id: str
    dataset_content_sha256: str
    sample_count: int = Field(gt=0)
    redaction_profile: str
    privacy_review_ids: tuple[str, ...] = Field(min_length=1)
    threshold_policy_id: str
    frozen: Literal[True] = True
    privacy_approved: Literal[True] = True
    unseen_before_reveal: Literal[True] = True
    release_holdout_eligible: Literal[True] = True


class ReleaseHoldoutResult(BaseModel):
    """Post-reveal evidence; it can no longer be represented as an unseen holdout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-release-holdout-result.v1"] = "eval-release-holdout-result.v1"
    result_id: str = Field(min_length=64, max_length=64)
    holdout_id: str
    proposal_id: str
    decision_id: str
    experiment_id: str
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_subject_id: str
    candidate_subject_id: str
    dataset_snapshot_id: str
    sample_count: int = Field(gt=0)
    passed: bool
    revealed_at: float
    evidence_class_after_reveal: Literal["development_gold"] = "development_gold"
    release_holdout_eligible: Literal[False] = False


class SubjectSelection(BaseModel):
    """Append-only activation or rollback pointer movement between immutable subjects."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-subject-selection.v1"] = "eval-subject-selection.v1"
    selection_id: str = Field(min_length=64, max_length=64)
    request_id: str
    kind: SubjectSelectionKind
    actor: str
    reason_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_subject_id: str
    selected_subject_id: str
    decision_id: str | None
    holdout_result_id: str | None
    rollback_of_selection_id: str | None
    selected_at: float


class PromotionLedger(BaseModel):
    """Safe append-only audit state; active_subject_id is the only mutable projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-promotion-ledger.v1"] = "eval-promotion-ledger.v1"
    active_subject_id: str = Field(min_length=1)
    decisions: tuple[HumanPromotionDecision, ...] = ()
    selections: tuple[SubjectSelection, ...] = ()


def record_promotion_decision(
    ledger: PromotionLedger,
    *,
    proposal: EvalChangeProposal,
    evaluation: ProposalEvaluation,
    report: EvalExperimentReport,
    request: HumanPromotionDecisionRequest,
) -> tuple[PromotionLedger, HumanPromotionDecision]:
    """Record explicit intent; even accept leaves the active subject unchanged."""

    _validate_development_evidence(proposal, evaluation, report)
    if request.decision == "accept" and report.evidence_state != "eligible_for_review":
        raise PromotionConflict("accept requires an experiment eligible for human review")
    if request.decision == "accept":
        resulting_state: PromotionCandidateState = "eligible_for_holdout"
    elif request.decision == "reject":
        resulting_state = "rejected"
    else:
        resulting_state = "experimental"
    report_sha256 = _report_hash(report)
    canonical = {
        "schema_version": "eval-promotion-decision.v1",
        "request_id": request.request_id,
        "proposal_id": proposal.proposal_id,
        "report_id": report.experiment_id,
        "report_sha256": report_sha256,
        "decision": request.decision,
        "actor": request.actor,
        "reason_sha256": _text_hash(request.reason),
        "decided_at": request.decided_at,
    }
    decision = HumanPromotionDecision(
        decision_id=_sha256(canonical),
        request_id=request.request_id,
        proposal_id=proposal.proposal_id,
        report_id=report.experiment_id,
        report_sha256=report_sha256,
        baseline_subject_id=proposal.baseline_subject_id,
        candidate_subject_id=proposal.candidate_subject.subject_id,
        decision=request.decision,
        resulting_state=resulting_state,
        actor=request.actor,
        reason_sha256=_text_hash(request.reason),
        decided_at=request.decided_at,
    )
    existing = next(
        (item for item in ledger.decisions if item.request_id == request.request_id),
        None,
    )
    if existing is not None:
        if existing == decision:
            return ledger, existing
        raise PromotionConflict("promotion decision idempotency conflict")
    if ledger.active_subject_id != proposal.baseline_subject_id:
        raise PromotionConflict("promotion decision targets a stale active subject")
    return ledger.model_copy(update={"decisions": (*ledger.decisions, decision)}), decision


def freeze_release_holdout(
    snapshot: DatasetSnapshotV1,
    *,
    request_id: str,
    threshold_policy_id: str,
    unseen_confirmed: bool,
) -> ReleaseHoldoutManifest:
    """Freeze only an all-blind, all-eligible, locally reviewed snapshot before reveal."""

    normalized_request = request_id.strip()
    normalized_policy = threshold_policy_id.strip()
    if not normalized_request or not normalized_policy:
        raise ValueError("holdout request and threshold policy must not be blank")
    if not unseen_confirmed:
        raise PromotionConflict("Release Holdout requires explicit unseen confirmation")
    if snapshot.candidate_count != len(snapshot.items) or snapshot.candidate_count == 0:
        raise PromotionConflict("Release Holdout snapshot counts are inconsistent")
    if (
        snapshot.eligible_blind_count != snapshot.candidate_count
        or snapshot.exploratory_count != 0
        or any(
            item.source_kind != "blind_grading_label" or not item.release_gate_eligible
            for item in snapshot.items
        )
    ):
        raise PromotionConflict("Release Holdout requires only eligible blind samples")
    privacy_review_ids = tuple(sorted({item.review_request_id for item in snapshot.items}))
    canonical = {
        "schema_version": "eval-release-holdout.v1",
        "request_id": normalized_request,
        "dataset_snapshot_id": snapshot.snapshot_id,
        "dataset_content_sha256": snapshot.content_sha256,
        "sample_count": snapshot.candidate_count,
        "redaction_profile": snapshot.redaction_profile,
        "privacy_review_ids": privacy_review_ids,
        "threshold_policy_id": normalized_policy,
    }
    return ReleaseHoldoutManifest(
        holdout_id=_sha256(canonical),
        request_id=normalized_request,
        dataset_snapshot_id=snapshot.snapshot_id,
        dataset_content_sha256=snapshot.content_sha256,
        sample_count=snapshot.candidate_count,
        redaction_profile=snapshot.redaction_profile,
        privacy_review_ids=privacy_review_ids,
        threshold_policy_id=normalized_policy,
    )


def evaluate_release_holdout(
    holdout: ReleaseHoldoutManifest,
    *,
    proposal: EvalChangeProposal,
    development_evaluation: ProposalEvaluation,
    decision: HumanPromotionDecision,
    experiment: PairedEvalExperiment,
    report: EvalExperimentReport,
    revealed_at: float,
) -> ReleaseHoldoutResult:
    """Reveal a fresh holdout only after accept; consume it regardless of pass/fail."""

    if (
        decision.proposal_id != proposal.proposal_id
        or decision.resulting_state != "eligible_for_holdout"
    ):
        raise PromotionConflict("Release Holdout requires an accepted human decision")
    if development_evaluation.proposal_id != proposal.proposal_id:
        raise PromotionConflict("development evidence does not belong to proposal")
    if holdout.dataset_snapshot_id == development_evaluation.dataset_snapshot_id:
        raise PromotionConflict("Release Holdout must be new and distinct from Development Gold")
    if (
        experiment.baseline_subject.subject_id != proposal.baseline_subject_id
        or experiment.candidate_subject.subject_id != proposal.candidate_subject.subject_id
    ):
        raise PromotionConflict("holdout experiment subject pairing does not match proposal")
    if (
        experiment.suite.dataset_snapshot_id != holdout.dataset_snapshot_id
        or experiment.suite.dataset_content_sha256 != holdout.dataset_content_sha256
    ):
        raise PromotionConflict("holdout experiment does not use the frozen snapshot")
    if report.experiment_id != experiment.experiment_id:
        raise PromotionConflict("holdout report does not belong to experiment")
    if report.policy_id != holdout.threshold_policy_id:
        raise PromotionConflict("holdout report does not use pre-registered thresholds")
    passed = report.evidence_state == "eligible_for_review"
    report_sha256 = _report_hash(report)
    canonical = {
        "schema_version": "eval-release-holdout-result.v1",
        "holdout_id": holdout.holdout_id,
        "proposal_id": proposal.proposal_id,
        "decision_id": decision.decision_id,
        "experiment_id": experiment.experiment_id,
        "report_sha256": report_sha256,
        "passed": passed,
        "revealed_at": revealed_at,
    }
    return ReleaseHoldoutResult(
        result_id=_sha256(canonical),
        holdout_id=holdout.holdout_id,
        proposal_id=proposal.proposal_id,
        decision_id=decision.decision_id,
        experiment_id=experiment.experiment_id,
        report_sha256=report_sha256,
        baseline_subject_id=proposal.baseline_subject_id,
        candidate_subject_id=proposal.candidate_subject.subject_id,
        dataset_snapshot_id=holdout.dataset_snapshot_id,
        sample_count=holdout.sample_count,
        passed=passed,
        revealed_at=revealed_at,
    )


def activate_candidate(
    ledger: PromotionLedger,
    *,
    decision_id: str,
    proposal: EvalChangeProposal,
    holdout: ReleaseHoldoutResult,
    request_id: str,
    actor: str,
    reason: str,
    selected_at: float,
) -> tuple[PromotionLedger, SubjectSelection]:
    """Select the exact candidate only after all human and holdout gates pass."""

    decision = next(
        (item for item in ledger.decisions if item.decision_id == decision_id),
        None,
    )
    if decision is None:
        raise PromotionConflict("activation requires a recorded human decision")
    if (
        decision.proposal_id != proposal.proposal_id
        or decision.resulting_state != "eligible_for_holdout"
    ):
        raise PromotionConflict("human decision does not authorize this candidate")
    if (
        not holdout.passed
        or holdout.proposal_id != proposal.proposal_id
        or holdout.decision_id != decision.decision_id
        or holdout.candidate_subject_id != proposal.candidate_subject.subject_id
    ):
        raise PromotionConflict("activation requires a passed matching Release Holdout")
    selection = _selection(
        request_id=request_id,
        kind="activation",
        actor=actor,
        reason=reason,
        previous_subject_id=proposal.baseline_subject_id,
        selected_subject_id=proposal.candidate_subject.subject_id,
        decision_id=decision.decision_id,
        holdout_result_id=holdout.result_id,
        rollback_of_selection_id=None,
        selected_at=selected_at,
    )
    existing = _selection_by_request(ledger, request_id)
    if existing is not None:
        if existing == selection:
            return ledger, existing
        raise PromotionConflict("subject selection idempotency conflict")
    if ledger.active_subject_id != proposal.baseline_subject_id:
        raise PromotionConflict("activation baseline is no longer active")
    return (
        ledger.model_copy(
            update={
                "active_subject_id": selection.selected_subject_id,
                "selections": (*ledger.selections, selection),
            }
        ),
        selection,
    )


def rollback_subject(
    ledger: PromotionLedger,
    *,
    activation_id: str,
    request_id: str,
    actor: str,
    reason: str,
    selected_at: float,
) -> tuple[PromotionLedger, SubjectSelection]:
    """Restore the exact previous subject recorded by one immutable activation."""

    activation = next(
        (
            item
            for item in ledger.selections
            if item.selection_id == activation_id and item.kind == "activation"
        ),
        None,
    )
    if activation is None:
        raise PromotionConflict("rollback requires an existing activation")
    selection = _selection(
        request_id=request_id,
        kind="rollback",
        actor=actor,
        reason=reason,
        previous_subject_id=activation.selected_subject_id,
        selected_subject_id=activation.previous_subject_id,
        decision_id=None,
        holdout_result_id=None,
        rollback_of_selection_id=activation.selection_id,
        selected_at=selected_at,
    )
    existing = _selection_by_request(ledger, request_id)
    if existing is not None:
        if existing == selection:
            return ledger, existing
        raise PromotionConflict("subject selection idempotency conflict")
    if ledger.active_subject_id != activation.selected_subject_id:
        raise PromotionConflict("rollback target is not the active subject")
    return (
        ledger.model_copy(
            update={
                "active_subject_id": selection.selected_subject_id,
                "selections": (*ledger.selections, selection),
            }
        ),
        selection,
    )


def _validate_development_evidence(
    proposal: EvalChangeProposal,
    evaluation: ProposalEvaluation,
    report: EvalExperimentReport,
) -> None:
    if evaluation.proposal_id != proposal.proposal_id:
        raise PromotionConflict("development evaluation does not belong to proposal")
    if (
        evaluation.baseline_subject_id != proposal.baseline_subject_id
        or evaluation.candidate_subject_id != proposal.candidate_subject.subject_id
    ):
        raise PromotionConflict("development evaluation subject identity does not match")
    if report.experiment_id != evaluation.experiment_id:
        raise PromotionConflict("development report does not belong to paired experiment")


def _selection(
    *,
    request_id: str,
    kind: SubjectSelectionKind,
    actor: str,
    reason: str,
    previous_subject_id: str,
    selected_subject_id: str,
    decision_id: str | None,
    holdout_result_id: str | None,
    rollback_of_selection_id: str | None,
    selected_at: float,
) -> SubjectSelection:
    normalized_request = request_id.strip()
    normalized_actor = actor.strip()
    normalized_reason = reason.strip()
    if not normalized_request or not normalized_actor or not normalized_reason:
        raise ValueError("selection request, actor, and reason must not be blank")
    canonical = {
        "schema_version": "eval-subject-selection.v1",
        "request_id": normalized_request,
        "kind": kind,
        "actor": normalized_actor,
        "reason_sha256": _text_hash(normalized_reason),
        "previous_subject_id": previous_subject_id,
        "selected_subject_id": selected_subject_id,
        "decision_id": decision_id,
        "holdout_result_id": holdout_result_id,
        "rollback_of_selection_id": rollback_of_selection_id,
        "selected_at": selected_at,
    }
    return SubjectSelection(
        selection_id=_sha256(canonical),
        request_id=normalized_request,
        kind=kind,
        actor=normalized_actor,
        reason_sha256=_text_hash(normalized_reason),
        previous_subject_id=previous_subject_id,
        selected_subject_id=selected_subject_id,
        decision_id=decision_id,
        holdout_result_id=holdout_result_id,
        rollback_of_selection_id=rollback_of_selection_id,
        selected_at=selected_at,
    )


def _selection_by_request(
    ledger: PromotionLedger,
    request_id: str,
) -> SubjectSelection | None:
    normalized = request_id.strip()
    return next((item for item in ledger.selections if item.request_id == normalized), None)


def _report_hash(report: EvalExperimentReport) -> str:
    return _sha256(report.model_dump(mode="json"))


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
