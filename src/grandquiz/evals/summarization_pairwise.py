"""Blind dual-judge plans for summarization routing evidence."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from grandquiz.evals.summarization_routing import (
    SummarizationPilotCollection,
    SummarizationPilotMessage,
    SummarizationPilotPlan,
)
from grandquiz.kernel.events import EventEmitter
from grandquiz.kernel.model_execution import complete_model_call
from grandquiz.providers.base import Message, Model, Usage
from grandquiz.providers.failure import ProviderFailure
from grandquiz.providers.models import fallback_plan_of, identity_of, retry_runtime_of
from grandquiz.providers.profiles import ModelConfiguration, ModelSelection

_PROMPT_PATH = Path(__file__).parent / "prompts" / "summarization_pairwise_judge.md"
DisplayLabel = Literal["A", "B", "tie", "both_bad", "exclude"]


class SummarizationJudgePlanError(ValueError):
    """The paired collection cannot form a safe blind-judge plan."""


class SummarizationJudgeBudgetExceeded(RuntimeError):
    """The judge plan cannot fit under the owner-approved experiment cap."""


class SummarizationJudgeApprovalRequired(PermissionError):
    """The exact frozen judge plan has not received an explicit Yes decision."""


class _JudgeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SummarizationJudgeCandidate(_JudgeRecord):
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)


class SummarizationJudgePolicy(_JudgeRecord):
    judges: tuple[SummarizationJudgeCandidate, SummarizationJudgeCandidate]
    experiment_token_cap: int = Field(gt=0, le=600_000)
    prior_actual_tokens: int = Field(ge=0)
    max_attempts_per_judge: Literal[1] = 1

    @field_validator("judges")
    @classmethod
    def _distinct_judges(
        cls,
        value: tuple[SummarizationJudgeCandidate, SummarizationJudgeCandidate],
    ) -> tuple[SummarizationJudgeCandidate, SummarizationJudgeCandidate]:
        if (
            len({judge.profile_id for judge in value}) != 2
            or len({judge.configuration_fingerprint for judge in value}) != 2
        ):
            raise ValueError("blind judges must be distinct deployments")
        return value

    @model_validator(mode="after")
    def _prior_usage_below_cap(self) -> Self:
        if self.prior_actual_tokens >= self.experiment_token_cap:
            raise ValueError("prior experiment usage already exhausts the token cap")
        return self


class SummarizationJudgeOutput(_JudgeRecord):
    candidate_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", repr=False)
    output: str = Field(min_length=1, repr=False)


class SummarizationJudgeAssignment(_JudgeRecord):
    judge_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    candidate_a_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", repr=False)
    candidate_b_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", repr=False)


class SummarizationJudgeCase(_JudgeRecord):
    case_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_group_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    messages: tuple[SummarizationPilotMessage, ...] = Field(min_length=2, repr=False)
    outputs: tuple[SummarizationJudgeOutput, SummarizationJudgeOutput] = Field(repr=False)
    assignments: tuple[SummarizationJudgeAssignment, SummarizationJudgeAssignment]


class SummarizationJudgeApprovalSummary(_JudgeRecord):
    consumer: Literal["summarization"] = "summarization"
    partition: Literal["development"] = "development"
    judge_profile_ids: tuple[str, str]
    judge_configuration_fingerprints: tuple[str, str]
    experiment_token_cap: int = Field(gt=0, le=600_000)
    prior_actual_tokens: int = Field(ge=0)
    reserved_tokens: int = Field(gt=0)
    max_attempts_per_judge: Literal[1] = 1
    prompt_version: str = Field(pattern=r"^summarization_pairwise_judge@[0-9a-f]{8}$")
    rubric_version: Literal["summarization_quality@v1"] = "summarization_quality@v1"
    case_count: int = Field(gt=0)
    external_call_count: int = Field(gt=0)


class SummarizationJudgePlan(_JudgeRecord):
    schema_version: Literal["summarization-routing-judge-plan.v1"] = (
        "summarization-routing-judge-plan.v1"
    )
    consumer: Literal["summarization"] = "summarization"
    partition: Literal["development"] = "development"
    pilot_plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    collection_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    judges: tuple[SummarizationJudgeCandidate, SummarizationJudgeCandidate]
    experiment_token_cap: int = Field(gt=0, le=600_000)
    prior_actual_tokens: int = Field(ge=0)
    reserved_tokens: int = Field(gt=0)
    max_attempts_per_judge: Literal[1] = 1
    prompt_version: str = Field(pattern=r"^summarization_pairwise_judge@[0-9a-f]{8}$")
    rubric_version: Literal["summarization_quality@v1"] = "summarization_quality@v1"
    cases: tuple[SummarizationJudgeCase, ...] = Field(min_length=1)
    approval_summary: SummarizationJudgeApprovalSummary
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SummarizationJudgeApproval(_JudgeRecord):
    schema_version: Literal["summarization-routing-judge-approval.v1"] = (
        "summarization-routing-judge-approval.v1"
    )
    approval_id: str = Field(min_length=1, max_length=256)
    plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    decided_at: float = Field(ge=0, allow_inf_nan=False)


class SummarizationCriterionScores(_JudgeRecord):
    factual_fidelity: int = Field(ge=1, le=4)
    useful_retention: int = Field(ge=1, le=4)
    compression_quality: int = Field(ge=1, le=4)
    continuation_usefulness: int = Field(ge=1, le=4)


class _JudgeVerdict(_JudgeRecord):
    preferred: Literal["A", "B", "tie", "both_bad"]
    a_scores: SummarizationCriterionScores
    b_scores: SummarizationCriterionScores
    rationale: str = Field(min_length=1, max_length=2_000)


class SummarizationJudgement(_JudgeRecord):
    judge_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    execution_status: Literal["completed", "invalid_verdict", "provider_error", "runtime_error"]
    suggested_label: str | None = None
    a_scores: SummarizationCriterionScores | None = None
    b_scores: SummarizationCriterionScores | None = None
    rationale: str | None = Field(default=None, repr=False)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    failure_category: str | None = None


class SummarizationJudgedCase(_JudgeRecord):
    case_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    suggested_label: str = Field(min_length=1, max_length=128)
    judgements: tuple[SummarizationJudgement, SummarizationJudgement]


class SummarizationJudgementCollection(_JudgeRecord):
    schema_version: Literal["summarization-routing-judgements.v1"] = (
        "summarization-routing-judgements.v1"
    )
    plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    known_actual_tokens: int = Field(ge=0)
    unknown_usage_count: int = Field(ge=0)
    cases: tuple[SummarizationJudgedCase, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SummarizationAggregateScores(_JudgeRecord):
    factual_fidelity: int = Field(ge=2, le=8)
    useful_retention: int = Field(ge=2, le=8)
    compression_quality: int = Field(ge=2, le=8)
    continuation_usefulness: int = Field(ge=2, le=8)


class SummarizationReviewCase(_JudgeRecord):
    case_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_reason: Literal["judge_disagreement", "agreement_audit"]
    messages: tuple[SummarizationPilotMessage, ...] = Field(min_length=2, repr=False)
    candidate_a_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", repr=False)
    candidate_b_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", repr=False)
    candidate_a_output: str = Field(min_length=1, repr=False)
    candidate_b_output: str = Field(min_length=1, repr=False)
    judge_votes: tuple[str, str]
    aggregate_a_scores: SummarizationAggregateScores
    aggregate_b_scores: SummarizationAggregateScores
    proposed_label: DisplayLabel


class SummarizationReviewPack(_JudgeRecord):
    schema_version: Literal["summarization-routing-review-pack.v1"] = (
        "summarization-routing-review-pack.v1"
    )
    judge_plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    judgements_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disagreement_count: int = Field(ge=0)
    audit_count: int = Field(ge=0)
    cases: tuple[SummarizationReviewCase, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def case_count(self) -> int:
        return len(self.cases)


class SummarizationReviewApproval(_JudgeRecord):
    schema_version: Literal["summarization-routing-review-approval.v1"] = (
        "summarization-routing-review-approval.v1"
    )
    approval_id: str = Field(min_length=1, max_length=256)
    review_pack_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    decided_at: float = Field(ge=0, allow_inf_nan=False)


def resolve_summarization_judge_candidates(
    configuration: ModelConfiguration,
    profile_ids: tuple[str, str],
) -> tuple[SummarizationJudgeCandidate, SummarizationJudgeCandidate]:
    """Freeze two explicit eval-quality deployments without reading credentials."""

    candidates: list[SummarizationJudgeCandidate] = []
    for profile_id in profile_ids:
        resolved = configuration.resolve_selected_candidates(
            "eval_quality",
            ModelSelection(profile_id=profile_id),
        )[0]
        context_window = resolved.profile.context_window_tokens
        max_output = resolved.profile.max_output_tokens
        if context_window is None or max_output is None:
            raise SummarizationJudgePlanError(
                "judge profiles require explicit context and output limits"
            )
        candidates.append(
            SummarizationJudgeCandidate(
                profile_id=profile_id,
                configuration_fingerprint=resolved.configuration_fingerprint,
                context_window_tokens=context_window,
                max_output_tokens=max_output,
            )
        )
    return (candidates[0], candidates[1])


def compile_summarization_judge_plan(
    pilot: SummarizationPilotPlan,
    collection: SummarizationPilotCollection,
    *,
    policy: SummarizationJudgePolicy,
) -> SummarizationJudgePlan:
    """Freeze development-only, identity-blind, reversed-order judge requests."""

    if collection.plan_content_sha256 != pilot.content_sha256:
        raise SummarizationJudgePlanError("collection does not belong to the pilot plan")
    result_by_case = {case.case_id: case for case in collection.cases}
    cases: list[SummarizationJudgeCase] = []
    candidate_ids = pilot.candidate_profile_ids
    for source in pilot.cases:
        if source.partition != "development":
            continue
        result = result_by_case.get(source.case_id)
        if result is None:
            raise SummarizationJudgePlanError("development case is missing from collection")
        outputs_by_candidate = {
            outcome.candidate_profile_id: outcome.output
            for outcome in result.outcomes
            if outcome.execution_status == "completed" and outcome.output is not None
        }
        if set(outputs_by_candidate) != set(candidate_ids):
            raise SummarizationJudgePlanError("development case lacks a completed candidate pair")
        first_order = (
            candidate_ids
            if int(source.case_id[:16], 16) % 2 == 0
            else (candidate_ids[1], candidate_ids[0])
        )
        second_order = (first_order[1], first_order[0])
        cases.append(
            SummarizationJudgeCase(
                case_id=source.case_id,
                source_group_id=source.source_group_id,
                messages=source.messages,
                outputs=(
                    SummarizationJudgeOutput(
                        candidate_profile_id=candidate_ids[0],
                        output=outputs_by_candidate[candidate_ids[0]],
                    ),
                    SummarizationJudgeOutput(
                        candidate_profile_id=candidate_ids[1],
                        output=outputs_by_candidate[candidate_ids[1]],
                    ),
                ),
                assignments=(
                    SummarizationJudgeAssignment(
                        judge_profile_id=policy.judges[0].profile_id,
                        candidate_a_profile_id=first_order[0],
                        candidate_b_profile_id=first_order[1],
                    ),
                    SummarizationJudgeAssignment(
                        judge_profile_id=policy.judges[1].profile_id,
                        candidate_a_profile_id=second_order[0],
                        candidate_b_profile_id=second_order[1],
                    ),
                ),
            )
        )
    if not cases:
        raise SummarizationJudgePlanError("pilot contains no development cases")

    prompt_text, prompt_version = _prompt()
    frozen_cases = tuple(sorted(cases, key=lambda case: case.case_id))
    reserved_tokens = _reserved_tokens(
        frozen_cases,
        judges=policy.judges,
        prompt_text=prompt_text,
    )
    if policy.prior_actual_tokens + reserved_tokens > policy.experiment_token_cap:
        raise SummarizationJudgeBudgetExceeded(
            "blind-judge reservation exceeds the remaining experiment token cap"
        )
    approval_summary = SummarizationJudgeApprovalSummary(
        judge_profile_ids=(policy.judges[0].profile_id, policy.judges[1].profile_id),
        judge_configuration_fingerprints=(
            policy.judges[0].configuration_fingerprint,
            policy.judges[1].configuration_fingerprint,
        ),
        experiment_token_cap=policy.experiment_token_cap,
        prior_actual_tokens=policy.prior_actual_tokens,
        reserved_tokens=reserved_tokens,
        prompt_version=prompt_version,
        case_count=len(frozen_cases),
        external_call_count=len(frozen_cases) * 2,
    )
    body = {
        "schema_version": "summarization-routing-judge-plan.v1",
        "consumer": "summarization",
        "partition": "development",
        "pilot_plan_content_sha256": pilot.content_sha256,
        "collection_content_sha256": collection.content_sha256,
        "judges": [judge.model_dump(mode="json") for judge in policy.judges],
        "experiment_token_cap": policy.experiment_token_cap,
        "prior_actual_tokens": policy.prior_actual_tokens,
        "reserved_tokens": reserved_tokens,
        "max_attempts_per_judge": 1,
        "prompt_version": prompt_version,
        "rubric_version": "summarization_quality@v1",
        "cases": [case.model_dump(mode="json") for case in frozen_cases],
    }
    return SummarizationJudgePlan(
        pilot_plan_content_sha256=pilot.content_sha256,
        collection_content_sha256=collection.content_sha256,
        judges=policy.judges,
        experiment_token_cap=policy.experiment_token_cap,
        prior_actual_tokens=policy.prior_actual_tokens,
        reserved_tokens=reserved_tokens,
        prompt_version=prompt_version,
        cases=frozen_cases,
        approval_summary=approval_summary,
        content_sha256=_digest(body),
    )


def approve_summarization_judge_plan(
    plan: SummarizationJudgePlan,
    *,
    approved: bool,
    approval_id: str,
    decided_at: float,
) -> SummarizationJudgeApproval:
    return SummarizationJudgeApproval(
        approval_id=approval_id,
        plan_content_sha256=plan.content_sha256,
        approved=approved,
        decided_at=decided_at,
    )


def require_approved_summarization_judge_plan(
    plan: SummarizationJudgePlan,
    *,
    approval: SummarizationJudgeApproval | None,
) -> SummarizationJudgePlan:
    if (
        approval is None
        or approval.approved is not True
        or approval.plan_content_sha256 != plan.content_sha256
    ):
        raise SummarizationJudgeApprovalRequired(
            "the exact blind-judge plan requires an explicit Yes approval"
        )
    return plan


async def collect_summarization_judgements(
    plan: SummarizationJudgePlan,
    *,
    approval: SummarizationJudgeApproval | None,
    judge_models: Mapping[str, Model],
    emitter: EventEmitter,
    monotonic: Callable[[], float] = time.monotonic,
) -> SummarizationJudgementCollection:
    """Run approved anonymous A/B comparisons and derive only agreement labels."""

    require_approved_summarization_judge_plan(plan, approval=approval)
    _validate_judge_models(plan, judge_models)
    prompt_text, prompt_version = _prompt()
    if prompt_version != plan.prompt_version:
        raise SummarizationJudgePlanError("judge prompt changed after plan approval")
    workflow_span = emitter.new_span_id()
    emitter.emit(
        "eval.summarization_judge.started",
        span_id=workflow_span,
        payload={
            "plan_content_sha256": plan.content_sha256,
            "case_count": len(plan.cases),
            "external_call_count": len(plan.cases) * 2,
            "experiment_token_cap": plan.experiment_token_cap,
            "prior_actual_tokens": plan.prior_actual_tokens,
            "reserved_tokens": plan.reserved_tokens,
        },
    )
    judged_cases: list[SummarizationJudgedCase] = []
    known_actual_tokens = 0
    unknown_usage_count = 0
    judge_by_id = {judge.profile_id: judge for judge in plan.judges}
    for case in plan.cases:
        outputs = {output.candidate_profile_id: output.output for output in case.outputs}
        judgements: list[SummarizationJudgement] = []
        for assignment in case.assignments:
            judge = judge_by_id[assignment.judge_profile_id]
            call_messages = _judge_messages(
                prompt_text,
                case,
                candidate_a=outputs[assignment.candidate_a_profile_id],
                candidate_b=outputs[assignment.candidate_b_profile_id],
            )
            input_upper_bound = _message_input_upper_bound(call_messages)
            call_span = emitter.new_span_id()
            emitter.emit(
                "eval.summarization_judge.call.started",
                span_id=call_span,
                parent_span_id=workflow_span,
                payload={
                    "case_id": case.case_id,
                    "judge_profile_id": judge.profile_id,
                },
            )
            started_at = monotonic()
            try:
                completion = await complete_model_call(
                    model=judge_models[judge.profile_id],
                    messages=call_messages,
                    emitter=emitter,
                    parent_span_id=call_span,
                    prompt_version=plan.prompt_version,
                    context={"case_id": case.case_id, "judge_profile_id": judge.profile_id},
                )
            except ProviderFailure as exc:
                latency_ms = max(0.0, (monotonic() - started_at) * 1_000)
                judgement = SummarizationJudgement(
                    judge_profile_id=judge.profile_id,
                    execution_status="provider_error",
                    latency_ms=latency_ms,
                    failure_category=exc.category.value,
                )
                unknown_usage_count += 1
            except Exception:
                latency_ms = max(0.0, (monotonic() - started_at) * 1_000)
                judgement = SummarizationJudgement(
                    judge_profile_id=judge.profile_id,
                    execution_status="runtime_error",
                    latency_ms=latency_ms,
                )
                unknown_usage_count += 1
            else:
                latency_ms = max(0.0, (monotonic() - started_at) * 1_000)
                if (
                    completion.usage.prompt_tokens > input_upper_bound
                    or completion.usage.completion_tokens > judge.max_output_tokens
                ):
                    raise SummarizationJudgeBudgetExceeded(
                        "judge usage exceeded the frozen per-call reservation"
                    )
                known_actual_tokens += completion.usage.total_tokens
                judgement = _parse_judgement(
                    completion.text,
                    assignment=assignment,
                    usage=completion.usage,
                    latency_ms=latency_ms,
                )
            judgements.append(judgement)
            emitter.emit(
                "eval.summarization_judge.call.ended",
                span_id=call_span,
                parent_span_id=workflow_span,
                payload={
                    "case_id": case.case_id,
                    "judge_profile_id": judge.profile_id,
                    "execution_status": judgement.execution_status,
                    "suggested_label": judgement.suggested_label,
                    "usage_status": ("known" if judgement.prompt_tokens is not None else "unknown"),
                },
            )
        labels: list[str] = []
        for judgement in judgements:
            if judgement.execution_status == "completed" and judgement.suggested_label is not None:
                labels.append(judgement.suggested_label)
        suggested_label = (
            labels[0] if len(labels) == 2 and labels[0] == labels[1] else "review_required"
        )
        judged_cases.append(
            SummarizationJudgedCase(
                case_id=case.case_id,
                suggested_label=suggested_label,
                judgements=(judgements[0], judgements[1]),
            )
        )

    frozen_cases = tuple(judged_cases)
    body = {
        "schema_version": "summarization-routing-judgements.v1",
        "plan_content_sha256": plan.content_sha256,
        "known_actual_tokens": known_actual_tokens,
        "unknown_usage_count": unknown_usage_count,
        "cases": [case.model_dump(mode="json") for case in frozen_cases],
    }
    result = SummarizationJudgementCollection(
        plan_content_sha256=plan.content_sha256,
        known_actual_tokens=known_actual_tokens,
        unknown_usage_count=unknown_usage_count,
        cases=frozen_cases,
        content_sha256=_digest(body),
    )
    emitter.emit(
        "eval.summarization_judge.ended",
        span_id=workflow_span,
        payload={
            "ok": True,
            "plan_content_sha256": plan.content_sha256,
            "judgements_content_sha256": result.content_sha256,
            "known_actual_tokens": known_actual_tokens,
            "unknown_usage_count": unknown_usage_count,
            "agreement_count": sum(
                case.suggested_label != "review_required" for case in result.cases
            ),
        },
    )
    return result


def build_summarization_review_pack(
    plan: SummarizationJudgePlan,
    judgements: SummarizationJudgementCollection,
    *,
    audit_sample_size: int = 5,
) -> SummarizationReviewPack:
    """Select all disagreements plus a deterministic audit sample, already labelled."""

    if audit_sample_size < 0:
        raise ValueError("audit sample size cannot be negative")
    if judgements.plan_content_sha256 != plan.content_sha256:
        raise SummarizationJudgePlanError("judgements do not belong to the judge plan")
    result_by_case = {case.case_id: case for case in judgements.cases}
    disagreements: list[SummarizationReviewCase] = []
    agreements: list[SummarizationReviewCase] = []
    for case in plan.cases:
        judged = result_by_case.get(case.case_id)
        if judged is None:
            raise SummarizationJudgePlanError("judge result is missing a planned case")
        review_case = _build_review_case(case, judged)
        if judged.suggested_label == "review_required":
            disagreements.append(
                review_case.model_copy(update={"selection_reason": "judge_disagreement"})
            )
        else:
            agreements.append(review_case)
    audits = _select_agreement_audits(agreements, audit_sample_size)
    selected = tuple(sorted([*disagreements, *audits], key=lambda case: case.case_id))
    if not selected:
        raise SummarizationJudgePlanError("review pack selection is empty")
    body = {
        "schema_version": "summarization-routing-review-pack.v1",
        "judge_plan_content_sha256": plan.content_sha256,
        "judgements_content_sha256": judgements.content_sha256,
        "disagreement_count": len(disagreements),
        "audit_count": len(audits),
        "cases": [case.model_dump(mode="json") for case in selected],
    }
    return SummarizationReviewPack(
        judge_plan_content_sha256=plan.content_sha256,
        judgements_content_sha256=judgements.content_sha256,
        disagreement_count=len(disagreements),
        audit_count=len(audits),
        cases=selected,
        content_sha256=_digest(body),
    )


def approve_summarization_review_pack(
    pack: SummarizationReviewPack,
    *,
    approved: bool,
    approval_id: str,
    decided_at: float,
) -> SummarizationReviewApproval:
    """Bind one human Yes/No decision to the exact generated review pack."""

    return SummarizationReviewApproval(
        approval_id=approval_id,
        review_pack_content_sha256=pack.content_sha256,
        approved=approved,
        decided_at=decided_at,
    )


def render_summarization_review_markdown(pack: SummarizationReviewPack) -> str:
    """Render the human surface without model or provider identities."""

    lines = [
        "# Summarization 路由盲审包",
        "",
        f"- 审查包哈希：`{pack.content_sha256}`",
        f"- 分歧样本：{pack.disagreement_count}",
        f"- 一致结果抽审：{pack.audit_count}",
        f"- 合计：{pack.case_count}",
        "",
        "请阅读原始对话与匿名候选。系统已经给出建议；无需填写标签。",
        "全部建议可接受时回复 **Yes**，任一建议不可接受时回复 **No**。",
        "",
    ]
    criterion_labels = (
        ("factual_fidelity", "事实忠实"),
        ("useful_retention", "关键信息保留"),
        ("compression_quality", "压缩质量"),
        ("continuation_usefulness", "续接可用性"),
    )
    for index, case in enumerate(pack.cases, start=1):
        reason = "双评审分歧" if case.selection_reason == "judge_disagreement" else "一致结果抽审"
        lines.extend(
            [
                f"## {index}. {case.case_id[:8]} · {reason}",
                "",
                "### 原始对话",
                "",
                _indented(
                    "\n".join(f"{message.role}：{message.content}" for message in case.messages)
                ),
                "",
                "### 候选 A",
                "",
                _indented(case.candidate_a_output),
                "",
                "### 候选 B",
                "",
                _indented(case.candidate_b_output),
                "",
                f"评审票：**{case.judge_votes[0]} / {case.judge_votes[1]}**",
                "",
                "| 维度 | A 汇总分 | B 汇总分 |",
                "| --- | ---: | ---: |",
            ]
        )
        for field_name, label in criterion_labels:
            lines.append(
                f"| {label} | {getattr(case.aggregate_a_scores, field_name)} "
                f"| {getattr(case.aggregate_b_scores, field_name)} |"
            )
        lines.extend(
            [
                "",
                "<details>",
                "<summary>查看系统建议</summary>",
                "",
                f"建议标签：**{case.proposed_label}**",
                "",
                "</details>",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


_SCORE_FIELDS = (
    "factual_fidelity",
    "useful_retention",
    "compression_quality",
    "continuation_usefulness",
)


def _build_review_case(
    case: SummarizationJudgeCase,
    judged: SummarizationJudgedCase,
) -> SummarizationReviewCase:
    totals = {
        output.candidate_profile_id: {field: 0 for field in _SCORE_FIELDS}
        for output in case.outputs
    }
    internal_votes: list[str] = []
    for assignment, judgement in zip(case.assignments, judged.judgements, strict=True):
        if (
            judgement.execution_status != "completed"
            or judgement.suggested_label is None
            or judgement.a_scores is None
            or judgement.b_scores is None
        ):
            raise SummarizationJudgePlanError("HITL pack requires two valid structured judgements")
        internal_votes.append(judgement.suggested_label)
        for field in _SCORE_FIELDS:
            totals[assignment.candidate_a_profile_id][field] += getattr(judgement.a_scores, field)
            totals[assignment.candidate_b_profile_id][field] += getattr(judgement.b_scores, field)

    profile_ids = tuple(output.candidate_profile_id for output in case.outputs)
    human_order = profile_ids if int(case.case_id[-16:], 16) % 2 == 0 else profile_ids[::-1]
    output_by_profile = {output.candidate_profile_id: output.output for output in case.outputs}
    proposed_internal = judged.suggested_label
    if proposed_internal == "review_required":
        first_total = sum(totals[profile_ids[0]].values())
        second_total = sum(totals[profile_ids[1]].values())
        if abs(first_total - second_total) < 2:
            proposed_internal = "tie"
        else:
            proposed_internal = profile_ids[0] if first_total > second_total else profile_ids[1]
    return SummarizationReviewCase(
        case_id=case.case_id,
        selection_reason="agreement_audit",
        messages=case.messages,
        candidate_a_profile_id=human_order[0],
        candidate_b_profile_id=human_order[1],
        candidate_a_output=output_by_profile[human_order[0]],
        candidate_b_output=output_by_profile[human_order[1]],
        judge_votes=(
            _display_label(internal_votes[0], human_order),
            _display_label(internal_votes[1], human_order),
        ),
        aggregate_a_scores=SummarizationAggregateScores(**totals[human_order[0]]),
        aggregate_b_scores=SummarizationAggregateScores(**totals[human_order[1]]),
        proposed_label=_display_label(proposed_internal, human_order),
    )


def _display_label(internal_label: str, human_order: tuple[str, ...]) -> DisplayLabel:
    if internal_label == human_order[0]:
        return "A"
    if internal_label == human_order[1]:
        return "B"
    if internal_label == "tie":
        return "tie"
    if internal_label == "both_bad":
        return "both_bad"
    if internal_label == "exclude":
        return "exclude"
    return "exclude"


def _select_agreement_audits(
    cases: list[SummarizationReviewCase],
    limit: int,
) -> list[SummarizationReviewCase]:
    if limit == 0:
        return []
    groups: dict[str, list[SummarizationReviewCase]] = {}
    for case in sorted(cases, key=lambda item: item.case_id):
        groups.setdefault(case.proposed_label, []).append(case)
    selected: list[SummarizationReviewCase] = []
    for label in sorted(groups):
        if len(selected) == limit:
            break
        selected.append(groups[label].pop(0))
    remaining = sorted(
        (case for group in groups.values() for case in group),
        key=lambda item: item.case_id,
    )
    selected.extend(remaining[: max(0, limit - len(selected))])
    return selected


def _indented(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def _validate_judge_models(
    plan: SummarizationJudgePlan,
    judge_models: Mapping[str, Model],
) -> None:
    if set(judge_models) != {judge.profile_id for judge in plan.judges}:
        raise SummarizationJudgePlanError("judge model set does not match the frozen plan")
    for judge in plan.judges:
        model = judge_models[judge.profile_id]
        identity = identity_of(model)
        if (
            identity is None
            or identity.purpose != "eval_quality"
            or identity.configuration_fingerprint != judge.configuration_fingerprint
        ):
            raise SummarizationJudgePlanError("judge model identity does not match the plan")
        runtime = retry_runtime_of(model)
        if runtime is not None and runtime.policy.enabled and runtime.policy.max_attempts != 1:
            raise SummarizationJudgePlanError("judge retries must be limited to one attempt")
        if fallback_plan_of(model) is not None:
            raise SummarizationJudgePlanError("blind judge does not permit provider fallback")


def _judge_messages(
    prompt_text: str,
    case: SummarizationJudgeCase,
    *,
    candidate_a: str,
    candidate_b: str,
) -> tuple[Message, Message]:
    source = "\n".join(f"{message.role}：{message.content}" for message in case.messages)
    return (
        Message(role="system", content=prompt_text),
        Message(
            role="user",
            content=f"原始对话：\n{source}\n\n候选 A：\n{candidate_a}\n\n候选 B：\n{candidate_b}",
        ),
    )


def _message_input_upper_bound(messages: Sequence[Message]) -> int:
    return sum(len(message.content.encode("utf-8")) + 32 for message in messages) + 448


def _normalize_json(text: str) -> str:
    stripped = text.strip()
    lines = stripped.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in {"```", "```json"}
        and lines[-1].strip() == "```"
        and all("```" not in line for line in lines[1:-1])
    ):
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _parse_judgement(
    text: str,
    *,
    assignment: SummarizationJudgeAssignment,
    usage: Usage,
    latency_ms: float,
) -> SummarizationJudgement:
    try:
        verdict = _JudgeVerdict.model_validate_json(_normalize_json(text))
    except ValueError:
        return SummarizationJudgement(
            judge_profile_id=assignment.judge_profile_id,
            execution_status="invalid_verdict",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            latency_ms=latency_ms,
        )
    label = {
        "A": assignment.candidate_a_profile_id,
        "B": assignment.candidate_b_profile_id,
        "tie": "tie",
        "both_bad": "both_bad",
    }[verdict.preferred]
    return SummarizationJudgement(
        judge_profile_id=assignment.judge_profile_id,
        execution_status="completed",
        suggested_label=label,
        a_scores=verdict.a_scores,
        b_scores=verdict.b_scores,
        rationale=verdict.rationale,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        latency_ms=latency_ms,
    )


def _prompt() -> tuple[str, str]:
    text = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return text, f"summarization_pairwise_judge@{fingerprint}"


def _reserved_tokens(
    cases: tuple[SummarizationJudgeCase, ...],
    *,
    judges: tuple[SummarizationJudgeCandidate, SummarizationJudgeCandidate],
    prompt_text: str,
) -> int:
    total = 0
    judge_by_id = {judge.profile_id: judge for judge in judges}
    for case in cases:
        outputs = {output.candidate_profile_id: output.output for output in case.outputs}
        for assignment in case.assignments:
            judge = judge_by_id[assignment.judge_profile_id]
            messages = _judge_messages(
                prompt_text,
                case,
                candidate_a=outputs[assignment.candidate_a_profile_id],
                candidate_b=outputs[assignment.candidate_b_profile_id],
            )
            input_upper_bound = _message_input_upper_bound(messages)
            if input_upper_bound + judge.max_output_tokens > judge.context_window_tokens:
                raise SummarizationJudgePlanError("judge request exceeds a context window")
            total += input_upper_bound + judge.max_output_tokens
    return total


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
