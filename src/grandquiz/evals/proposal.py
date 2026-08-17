"""Typed, append-only Eval change proposals from explicitly approved feedback.

This module deliberately stops at candidate construction and experiment binding. It does not
know where active prompts live and cannot mutate Providers, learning facts, or datasets.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from grandquiz.domain.learning.eval_inbox import EvalInboxCandidateV1
from grandquiz.evals.case import EvalSurface
from grandquiz.evals.experiment import PairedEvalExperiment
from grandquiz.evals.experiment_report import EvalExperimentReport
from grandquiz.evals.subject import EvalSubjectSnapshot, snapshot_subject

FeedbackSourceKind = Literal["eval_inbox", "experiment_failure_slice"]
FeedbackEvidenceClass = Literal["exploratory", "development_gold"]
ProposalChangeType = Literal["prompt", "policy"]

_SECRET_PATTERNS = (
    "authorization:",
    "bearer ",
    "api_key",
    "apikey",
    "password=",
    "refresh_token",
    "access_token",
    "token=",
    "sk-",
)


class ProposalConflict(ValueError):
    """A proposal command violates provenance, identity, or append-only history."""


class ApprovedFeedbackEvidence(BaseModel):
    """Safe immutable provenance; it carries identities, never private sample bodies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-approved-feedback.v1"] = "eval-approved-feedback.v1"
    feedback_id: str = Field(min_length=64, max_length=64)
    source_kind: FeedbackSourceKind
    source_ids: tuple[str, ...] = Field(min_length=1)
    source_hashes: tuple[str, ...] = Field(min_length=1)
    surfaces: tuple[EvalSurface, ...] = Field(min_length=1)
    slice_ids: tuple[str, ...]
    approval_request_id: str = Field(min_length=1)
    approval_reason: str = Field(min_length=1)
    approved_at: float | None
    evidence_class: FeedbackEvidenceClass
    release_holdout_eligible: Literal[False] = False

    @model_validator(mode="after")
    def _canonical_identity_lists(self) -> Self:
        for values, name in (
            (self.source_ids, "source_ids"),
            (self.source_hashes, "source_hashes"),
            (self.surfaces, "surfaces"),
            (self.slice_ids, "slice_ids"),
        ):
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{name} must be unique and canonically sorted")
        return self


class ChangeProposalRequest(BaseModel):
    """One explicit request to change one allow-listed subject binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1)
    target_surface: EvalSurface
    change_type: ProposalChangeType
    target_key: str = Field(min_length=1)
    expected_base_binding: str = Field(min_length=1)
    candidate_version: str = Field(min_length=1)
    draft_content: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    supersedes_proposal_id: str | None = None

    @field_validator(
        "request_id",
        "target_key",
        "expected_base_binding",
        "candidate_version",
        "draft_content",
        "rationale",
    )
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("proposal text fields must not be blank")
        return normalized


class EvalChangeProposal(BaseModel):
    """Immutable candidate artifact; this is not an active production configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-change-proposal.v1"] = "eval-change-proposal.v1"
    proposal_id: str = Field(min_length=64, max_length=64)
    request_id: str
    feedback: ApprovedFeedbackEvidence
    baseline_subject_id: str
    target_surface: EvalSurface
    change_type: ProposalChangeType
    target_key: str
    expected_base_binding: str
    candidate_version: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_content: str
    rationale: str
    candidate_subject: EvalSubjectSnapshot
    supersedes_proposal_id: str | None
    execution_scope: Literal["eval_candidate_only"] = "eval_candidate_only"
    production_side_effects_allowed: Literal[False] = False


class ProposalLedger(BaseModel):
    """Local append-only proposal history; persistence waits for a real UI consumer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-proposal-ledger.v1"] = "eval-proposal-ledger.v1"
    proposals: tuple[EvalChangeProposal, ...] = ()

    def active(self) -> tuple[EvalChangeProposal, ...]:
        superseded = {
            proposal.supersedes_proposal_id
            for proposal in self.proposals
            if proposal.supersedes_proposal_id is not None
        }
        return tuple(
            proposal for proposal in self.proposals if proposal.proposal_id not in superseded
        )


class ProposalEvaluation(BaseModel):
    """Proof that one proposal used the canonical paired-experiment interface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-proposal-evaluation.v1"] = "eval-proposal-evaluation.v1"
    proposal_id: str
    experiment_id: str
    baseline_subject_id: str
    candidate_subject_id: str
    dataset_snapshot_id: str
    dataset_content_sha256: str


def approved_feedback_from_inbox(
    candidate: EvalInboxCandidateV1,
) -> ApprovedFeedbackEvidence:
    """Project one locally approved active Inbox item without leaking its payload."""

    if candidate.lifecycle_status != "active" or candidate.review_status != "approved":
        raise ProposalConflict("feedback requires an approved active Eval Inbox candidate")
    if (
        candidate.review_request_id is None
        or candidate.review_reason is None
        or candidate.reviewed_at is None
    ):
        raise ProposalConflict("approved feedback is missing review provenance")
    evidence_class: FeedbackEvidenceClass = (
        "exploratory" if candidate.source_kind == "verdict_correction" else "development_gold"
    )
    canonical = {
        "schema_version": "eval-approved-feedback.v1",
        "source_kind": "eval_inbox",
        "source_ids": [candidate.candidate_id],
        "source_hashes": [candidate.payload_hash],
        "surfaces": ["answer_grading"],
        "slice_ids": [],
        "approval_request_id": candidate.review_request_id,
        "approval_reason": candidate.review_reason,
        "approved_at": candidate.reviewed_at,
        "evidence_class": evidence_class,
    }
    return ApprovedFeedbackEvidence(
        feedback_id=_sha256(canonical),
        source_kind="eval_inbox",
        source_ids=(candidate.candidate_id,),
        source_hashes=(candidate.payload_hash,),
        surfaces=("answer_grading",),
        slice_ids=(),
        approval_request_id=candidate.review_request_id,
        approval_reason=candidate.review_reason,
        approved_at=candidate.reviewed_at,
        evidence_class=evidence_class,
    )


def approved_feedback_from_failure_slices(
    report: EvalExperimentReport,
    *,
    slice_ids: tuple[str, ...],
    approval_request_id: str,
    reviewer: str,
    reason: str,
) -> ApprovedFeedbackEvidence:
    """Record an explicit human approval of report slices as Development Gold."""

    normalized_approval = approval_request_id.strip()
    normalized_reviewer = reviewer.strip()
    normalized_reason = reason.strip()
    if not normalized_approval or not normalized_reviewer or not normalized_reason:
        raise ValueError("failure-slice approval provenance must not be blank")
    canonical_slices = tuple(sorted(set(slice_ids)))
    if not canonical_slices:
        raise ValueError("failure-slice approval requires at least one slice")
    report_slices = {item.slice_id for item in report.slice_summaries}
    if not set(canonical_slices) <= report_slices:
        raise ProposalConflict("failure-slice approval references an unknown slice")
    matching_samples = [sample for sample in report.samples if sample.slice_id in canonical_slices]
    surfaces = cast(
        "tuple[EvalSurface, ...]",
        tuple(sorted({sample.surface for sample in matching_samples})),
    )
    source_hash = _sha256(
        {
            "experiment_id": report.experiment_id,
            "policy_id": report.policy_id,
            "slice_ids": canonical_slices,
        }
    )
    approval_reason = f"{normalized_reviewer}: {normalized_reason}"
    canonical = {
        "schema_version": "eval-approved-feedback.v1",
        "source_kind": "experiment_failure_slice",
        "source_ids": [report.experiment_id],
        "source_hashes": [source_hash],
        "surfaces": surfaces,
        "slice_ids": canonical_slices,
        "approval_request_id": normalized_approval,
        "approval_reason": approval_reason,
        "approved_at": None,
        "evidence_class": "development_gold",
    }
    return ApprovedFeedbackEvidence(
        feedback_id=_sha256(canonical),
        source_kind="experiment_failure_slice",
        source_ids=(report.experiment_id,),
        source_hashes=(source_hash,),
        surfaces=surfaces,
        slice_ids=canonical_slices,
        approval_request_id=normalized_approval,
        approval_reason=approval_reason,
        approved_at=None,
        evidence_class="development_gold",
    )


def propose_change(
    ledger: ProposalLedger,
    *,
    baseline_subject: EvalSubjectSnapshot,
    feedback: ApprovedFeedbackEvidence,
    request: ChangeProposalRequest,
) -> tuple[ProposalLedger, EvalChangeProposal]:
    """Build one bounded candidate subject without touching production state."""

    if request.target_surface not in feedback.surfaces:
        raise ProposalConflict("proposal surface is not supported by approved feedback")
    _reject_secret_text("candidate_version", request.candidate_version)
    _reject_secret_text("draft_content", request.draft_content)
    _reject_secret_text("rationale", request.rationale)

    bindings = (
        dict(baseline_subject.prompts)
        if request.change_type == "prompt"
        else dict(baseline_subject.policies)
    )
    current = bindings.get(request.target_key)
    if current is None:
        raise ProposalConflict("proposal target is not an existing allow-listed binding")
    if current != request.expected_base_binding:
        raise ProposalConflict("proposal expected base binding does not match subject")
    if request.candidate_version == request.expected_base_binding:
        raise ProposalConflict("candidate version must differ from the base binding")

    superseded: EvalChangeProposal | None = None
    if request.supersedes_proposal_id is not None:
        superseded = next(
            (
                proposal
                for proposal in ledger.proposals
                if proposal.proposal_id == request.supersedes_proposal_id
            ),
            None,
        )
        if superseded is None:
            raise ProposalConflict("superseded proposal does not exist")
        if superseded not in ledger.active():
            raise ProposalConflict("only an active proposal can be superseded")
        if (
            superseded.baseline_subject_id,
            superseded.target_surface,
            superseded.change_type,
            superseded.target_key,
        ) != (
            baseline_subject.subject_id,
            request.target_surface,
            request.change_type,
            request.target_key,
        ):
            raise ProposalConflict("superseding proposal must keep the same bounded target")

    content_sha256 = hashlib.sha256(request.draft_content.encode("utf-8")).hexdigest()
    candidate_binding = f"{request.candidate_version}#sha256:{content_sha256}"
    bindings[request.target_key] = candidate_binding
    candidate_subject = snapshot_subject(
        prompts=(bindings if request.change_type == "prompt" else dict(baseline_subject.prompts)),
        providers=baseline_subject.providers,
        tool_schemas=dict(baseline_subject.tool_schemas),
        policies=(bindings if request.change_type == "policy" else dict(baseline_subject.policies)),
    )
    canonical = {
        "schema_version": "eval-change-proposal.v1",
        "request": request.model_dump(mode="json"),
        "feedback_id": feedback.feedback_id,
        "baseline_subject_id": baseline_subject.subject_id,
        "candidate_subject_id": candidate_subject.subject_id,
        "content_sha256": content_sha256,
    }
    proposal = EvalChangeProposal(
        proposal_id=_sha256(canonical),
        request_id=request.request_id,
        feedback=feedback,
        baseline_subject_id=baseline_subject.subject_id,
        target_surface=request.target_surface,
        change_type=request.change_type,
        target_key=request.target_key,
        expected_base_binding=request.expected_base_binding,
        candidate_version=request.candidate_version,
        content_sha256=content_sha256,
        draft_content=request.draft_content,
        rationale=request.rationale,
        candidate_subject=candidate_subject,
        supersedes_proposal_id=request.supersedes_proposal_id,
    )

    existing = next(
        (item for item in ledger.proposals if item.request_id == request.request_id),
        None,
    )
    if existing is not None:
        if existing == proposal:
            return ledger, existing
        raise ProposalConflict("proposal request idempotency conflict")
    return ledger.model_copy(update={"proposals": (*ledger.proposals, proposal)}), proposal


def bind_proposal_experiment(
    proposal: EvalChangeProposal,
    experiment: PairedEvalExperiment,
) -> ProposalEvaluation:
    """Accept evidence only from the canonical exact baseline/candidate pairing."""

    if experiment.baseline_subject.subject_id != proposal.baseline_subject_id:
        raise ProposalConflict("experiment baseline subject does not match proposal")
    if experiment.candidate_subject.subject_id != proposal.candidate_subject.subject_id:
        raise ProposalConflict("experiment candidate subject does not match proposal")
    return ProposalEvaluation(
        proposal_id=proposal.proposal_id,
        experiment_id=experiment.experiment_id,
        baseline_subject_id=experiment.baseline_subject.subject_id,
        candidate_subject_id=experiment.candidate_subject.subject_id,
        dataset_snapshot_id=experiment.suite.dataset_snapshot_id,
        dataset_content_sha256=experiment.suite.dataset_content_sha256,
    )


def _reject_secret_text(name: str, value: str) -> None:
    lowered = value.casefold()
    if any(pattern in lowered for pattern in _SECRET_PATTERNS):
        raise ValueError(f"secret-bearing proposal text is forbidden: {name}")


def _sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
