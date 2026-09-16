"""Approval-bound input plans for the summarization routing pilot.

This module only compiles already-recorded local trace events into a frozen plan.
It never calls a provider.  The approval surface deliberately contains counts and
digests, not conversation text.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.summarizer import LLMSummarizer
from grandquiz.evals.rubrics import get_rubric
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Model, ToolSpec
from grandquiz.providers.failure import ProviderFailure
from grandquiz.providers.models import (
    ModelFallbackPlan,
    fallback_plan_of,
    identity_of,
    retry_runtime_of,
)
from grandquiz.providers.profiles import ModelConfiguration, ModelIdentity, ModelSelection
from grandquiz.providers.retry import RetryRuntime


class SummarizationPilotError(ValueError):
    """Trace evidence cannot be compiled into an unambiguous pilot plan."""


class SummarizationPilotApprovalRequired(PermissionError):
    """The exact frozen plan has not received an explicit Yes decision."""


class SummarizationPilotBudgetExceeded(RuntimeError):
    """The conservative pre-call reservation cannot fit the approved token cap."""


class _PilotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SummarizationPilotCandidate(_PilotRecord):
    """One explicit deployment identity and its enforceable request limits."""

    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)


class SummarizationPilotPolicy(_PilotRecord):
    """Owner-approved bounds for preparing, but not executing, the pilot."""

    candidates: tuple[SummarizationPilotCandidate, SummarizationPilotCandidate]
    max_total_tokens: int = Field(gt=0, le=600_000)
    max_cases: int = Field(default=40, gt=0, le=1_000)
    max_turns_per_case: int = Field(default=5, gt=0, le=20)
    holdout_every: int = Field(default=5, ge=2, le=100)
    max_attempts_per_candidate: Literal[1] = 1

    @field_validator("candidates")
    @classmethod
    def _distinct_profiles(
        cls,
        value: tuple[SummarizationPilotCandidate, SummarizationPilotCandidate],
    ) -> tuple[SummarizationPilotCandidate, SummarizationPilotCandidate]:
        profile_ids = [candidate.profile_id for candidate in value]
        fingerprints = [candidate.configuration_fingerprint for candidate in value]
        if len(set(profile_ids)) != len(profile_ids):
            raise ValueError("candidate profile ids must be distinct")
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("candidate deployments must be distinct")
        return value

    @property
    def candidate_profile_ids(self) -> tuple[str, str]:
        return (self.candidates[0].profile_id, self.candidates[1].profile_id)


class SummarizationPilotMessage(_PilotRecord):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, repr=False)


class SummarizationPilotCase(_PilotRecord):
    case_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_group_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    partition: Literal["development", "holdout"]
    prior_summary: Literal[""] = ""
    messages: tuple[SummarizationPilotMessage, ...] = Field(min_length=2)


class SummarizationPilotApprovalSummary(_PilotRecord):
    """Safe facts that may be shown before conversation text is released."""

    consumer: Literal["summarization"] = "summarization"
    data_scope: Literal["successful_local_chat_turns"] = "successful_local_chat_turns"
    candidate_profile_ids: tuple[str, str]
    candidate_configuration_fingerprints: tuple[str, str]
    max_total_tokens: int = Field(gt=0, le=600_000)
    reserved_total_tokens: int = Field(gt=0)
    max_attempts_per_candidate: Literal[1] = 1
    prompt_version: str = Field(pattern=r"^summarize@[0-9a-f]{8}$")
    rubric_version: Literal["summarization_quality@v1"] = "summarization_quality@v1"
    case_count: int = Field(gt=0)
    source_group_count: int = Field(gt=0)
    development_case_count: int = Field(ge=0)
    holdout_case_count: int = Field(ge=0)
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class SummarizationPilotPlan(_PilotRecord):
    schema_version: Literal["summarization-routing-pilot-plan.v1"] = (
        "summarization-routing-pilot-plan.v1"
    )
    consumer: Literal["summarization"] = "summarization"
    data_scope: Literal["successful_local_chat_turns"] = "successful_local_chat_turns"
    candidates: tuple[SummarizationPilotCandidate, SummarizationPilotCandidate]
    max_total_tokens: int = Field(gt=0, le=600_000)
    reserved_total_tokens: int = Field(gt=0)
    max_attempts_per_candidate: Literal[1] = 1
    prompt_version: str = Field(pattern=r"^summarize@[0-9a-f]{8}$")
    rubric_id: Literal["summarization_quality"] = "summarization_quality"
    rubric_version: Literal["summarization_quality@v1"] = "summarization_quality@v1"
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[SummarizationPilotCase, ...] = Field(min_length=1)
    approval_summary: SummarizationPilotApprovalSummary
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def candidate_profile_ids(self) -> tuple[str, str]:
        return (self.candidates[0].profile_id, self.candidates[1].profile_id)

    @model_validator(mode="after")
    def _derived_facts_match_content(self) -> Self:
        expected_summary = _approval_summary(
            candidates=self.candidates,
            max_total_tokens=self.max_total_tokens,
            reserved_total_tokens=self.reserved_total_tokens,
            prompt_version=self.prompt_version,
            source_revision=self.source_revision,
            cases=self.cases,
        )
        if self.approval_summary != expected_summary:
            raise ValueError("pilot approval summary does not match frozen cases")
        if self.content_sha256 != _plan_digest(
            candidates=self.candidates,
            max_total_tokens=self.max_total_tokens,
            reserved_total_tokens=self.reserved_total_tokens,
            prompt_version=self.prompt_version,
            source_revision=self.source_revision,
            cases=self.cases,
        ):
            raise ValueError("pilot plan content digest does not match frozen cases")
        return self


class SummarizationPilotApproval(_PilotRecord):
    schema_version: Literal["summarization-routing-pilot-approval.v1"] = (
        "summarization-routing-pilot-approval.v1"
    )
    approval_id: str = Field(min_length=1, max_length=256)
    plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    decided_at: float = Field(ge=0, allow_inf_nan=False)


class SummarizationPilotOutcome(_PilotRecord):
    candidate_profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    execution_status: Literal["completed", "contract_error", "provider_error", "runtime_error"]
    output: str | None = Field(default=None, repr=False)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    failure_category: str | None = None

    @model_validator(mode="after")
    def _status_matches_result(self) -> Self:
        if self.execution_status == "completed":
            if not self.output or self.prompt_tokens is None or self.completion_tokens is None:
                raise ValueError("completed pilot outcome requires output and known usage")
            if self.failure_category is not None:
                raise ValueError("completed pilot outcome cannot carry a failure category")
        elif self.execution_status == "contract_error":
            if (
                self.output is not None
                or self.prompt_tokens is None
                or self.completion_tokens is None
            ):
                raise ValueError("contract error requires known usage and no accepted output")
        elif any(
            value is not None for value in (self.output, self.prompt_tokens, self.completion_tokens)
        ):
            raise ValueError("failed pilot outcome cannot claim an output or token usage")
        if self.execution_status == "provider_error" and self.failure_category is None:
            raise ValueError("provider failure requires its normalized category")
        if self.execution_status != "provider_error" and self.failure_category is not None:
            raise ValueError("only provider failures carry a provider category")
        return self


class SummarizationPilotCaseResult(_PilotRecord):
    case_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    partition: Literal["development", "holdout"]
    outcomes: tuple[SummarizationPilotOutcome, SummarizationPilotOutcome]


class SummarizationPilotCollection(_PilotRecord):
    schema_version: Literal["summarization-routing-pilot-collection.v1"] = (
        "summarization-routing-pilot-collection.v1"
    )
    plan_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(pattern=r"^summarize@[0-9a-f]{8}$")
    rubric_version: Literal["summarization_quality@v1"] = "summarization_quality@v1"
    reserved_tokens: int = Field(gt=0)
    known_actual_tokens: int = Field(ge=0)
    unknown_usage_count: int = Field(ge=0)
    cases: tuple[SummarizationPilotCaseResult, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _digest_matches_collection(self) -> Self:
        if self.content_sha256 != _collection_digest(
            plan_content_sha256=self.plan_content_sha256,
            prompt_version=self.prompt_version,
            rubric_version=self.rubric_version,
            reserved_tokens=self.reserved_tokens,
            known_actual_tokens=self.known_actual_tokens,
            unknown_usage_count=self.unknown_usage_count,
            cases=self.cases,
        ):
            raise ValueError("pilot collection digest does not match its outcomes")
        return self


@dataclass
class _CapturingModel:
    inner: Model
    completion: Completion | None = None

    @property
    def identity(self) -> ModelIdentity | None:
        return identity_of(self.inner)

    @property
    def retry_runtime(self) -> RetryRuntime | None:
        return retry_runtime_of(self.inner)

    @property
    def fallback_plan(self) -> ModelFallbackPlan | None:
        return fallback_plan_of(self.inner)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        completion = await self.inner.complete(messages, tools=tools)
        self.completion = completion
        return completion


def compile_summarization_pilot(
    traces: Mapping[str, Sequence[AgentEvent]],
    *,
    policy: SummarizationPilotPolicy,
) -> SummarizationPilotPlan:
    """Compile successful chat turns into an order-independent frozen plan."""

    cases: list[SummarizationPilotCase] = []
    for source_trace_id, events in traces.items():
        case = _compile_trace_case(
            source_trace_id,
            events,
            max_turns=policy.max_turns_per_case,
            holdout_every=policy.holdout_every,
        )
        if case is not None:
            cases.append(case)
    cases.sort(key=lambda item: item.case_id)
    cases = cases[: policy.max_cases]
    if not cases:
        raise SummarizationPilotError("no successful chat traces are eligible for the pilot")

    frozen_cases = tuple(cases)
    source_revision = _digest([case.case_id for case in frozen_cases])
    prompt = load_prompt("summarize")
    prompt_version = prompt.version
    rubric = get_rubric("summarization_quality")
    if rubric is None or rubric.version != "summarization_quality@v1":
        raise SummarizationPilotError("summarization quality rubric is not registered")
    rubric_version: Literal["summarization_quality@v1"] = "summarization_quality@v1"
    reserved_total_tokens = _reserved_total_tokens(
        cases=frozen_cases,
        candidates=policy.candidates,
        prompt_text=prompt.text,
    )
    if reserved_total_tokens > policy.max_total_tokens:
        raise SummarizationPilotBudgetExceeded(
            "summarization pilot reservation exceeds the approved token cap"
        )
    approval_summary = _approval_summary(
        candidates=policy.candidates,
        max_total_tokens=policy.max_total_tokens,
        reserved_total_tokens=reserved_total_tokens,
        prompt_version=prompt_version,
        source_revision=source_revision,
        cases=frozen_cases,
    )
    content_sha256 = _plan_digest(
        candidates=policy.candidates,
        max_total_tokens=policy.max_total_tokens,
        reserved_total_tokens=reserved_total_tokens,
        prompt_version=prompt_version,
        source_revision=source_revision,
        cases=frozen_cases,
    )
    return SummarizationPilotPlan(
        candidates=policy.candidates,
        max_total_tokens=policy.max_total_tokens,
        reserved_total_tokens=reserved_total_tokens,
        prompt_version=prompt_version,
        rubric_version=rubric_version,
        source_revision=source_revision,
        cases=frozen_cases,
        approval_summary=approval_summary,
        content_sha256=content_sha256,
    )


def approve_summarization_pilot(
    plan: SummarizationPilotPlan,
    *,
    approved: bool,
    approval_id: str,
    decided_at: float,
) -> SummarizationPilotApproval:
    """Bind one explicit Yes/No decision to the exact frozen plan digest."""

    return SummarizationPilotApproval(
        approval_id=approval_id,
        plan_content_sha256=plan.content_sha256,
        approved=approved,
        decided_at=decided_at,
    )


def require_approved_summarization_pilot(
    plan: SummarizationPilotPlan,
    *,
    approval: SummarizationPilotApproval | None,
) -> SummarizationPilotPlan:
    """Release only the exact plan named by an affirmative approval record."""

    if (
        approval is None
        or approval.approved is not True
        or approval.plan_content_sha256 != plan.content_sha256
    ):
        raise SummarizationPilotApprovalRequired(
            "the exact summarization pilot plan requires an explicit Yes approval"
        )
    return plan


async def collect_summarization_pilot(
    plan: SummarizationPilotPlan,
    *,
    approval: SummarizationPilotApproval | None,
    candidate_models: Mapping[str, Model],
    emitter: EventEmitter,
    monotonic: Callable[[], float] = time.monotonic,
) -> SummarizationPilotCollection:
    """Run one approved paired collection through the production summarizer consumer."""

    require_approved_summarization_pilot(plan, approval=approval)
    _validate_candidate_models(plan, candidate_models)
    workflow_span = emitter.new_span_id()
    emitter.emit(
        "eval.summarization_pilot.started",
        span_id=workflow_span,
        payload={
            "plan_content_sha256": plan.content_sha256,
            "case_count": len(plan.cases),
            "candidate_count": len(plan.candidates),
            "max_total_tokens": plan.max_total_tokens,
            "reserved_tokens": plan.reserved_total_tokens,
        },
    )

    case_results: list[SummarizationPilotCaseResult] = []
    known_actual_tokens = 0
    unknown_usage_count = 0
    for case in plan.cases:
        outcomes: list[SummarizationPilotOutcome] = []
        input_upper_bound = _case_input_upper_bound(case, load_prompt("summarize").text)
        provider_messages = tuple(
            Message(role=message.role, content=message.content) for message in case.messages
        )
        for candidate in plan.candidates:
            candidate_span = emitter.new_span_id()
            emitter.emit(
                "eval.summarization_pilot.candidate.started",
                span_id=candidate_span,
                parent_span_id=workflow_span,
                payload={
                    "case_id": case.case_id,
                    "candidate_profile_id": candidate.profile_id,
                    "partition": case.partition,
                },
            )
            capture = _CapturingModel(candidate_models[candidate.profile_id])
            started_at = monotonic()
            try:
                summary = await LLMSummarizer(capture, emitter).summarize(
                    case.prior_summary,
                    provider_messages,
                )
            except ProviderFailure as exc:
                latency_ms = max(0.0, (monotonic() - started_at) * 1_000)
                outcome = SummarizationPilotOutcome(
                    candidate_profile_id=candidate.profile_id,
                    execution_status="provider_error",
                    latency_ms=latency_ms,
                    failure_category=exc.category.value,
                )
                unknown_usage_count += 1
            except Exception:
                latency_ms = max(0.0, (monotonic() - started_at) * 1_000)
                outcome = SummarizationPilotOutcome(
                    candidate_profile_id=candidate.profile_id,
                    execution_status="runtime_error",
                    latency_ms=latency_ms,
                )
                unknown_usage_count += 1
            else:
                latency_ms = max(0.0, (monotonic() - started_at) * 1_000)
                completion = capture.completion
                if completion is None:
                    raise SummarizationPilotError("summarizer completed without captured usage")
                if (
                    completion.usage.prompt_tokens > input_upper_bound
                    or completion.usage.completion_tokens > candidate.max_output_tokens
                ):
                    raise SummarizationPilotBudgetExceeded(
                        "provider usage exceeded the frozen per-call reservation"
                    )
                known_actual_tokens += completion.usage.total_tokens
                outcome = SummarizationPilotOutcome(
                    candidate_profile_id=candidate.profile_id,
                    execution_status="completed" if summary else "contract_error",
                    output=summary or None,
                    prompt_tokens=completion.usage.prompt_tokens,
                    completion_tokens=completion.usage.completion_tokens,
                    latency_ms=latency_ms,
                )
            outcomes.append(outcome)
            emitter.emit(
                "eval.summarization_pilot.candidate.ended",
                span_id=candidate_span,
                parent_span_id=workflow_span,
                payload={
                    "case_id": case.case_id,
                    "candidate_profile_id": candidate.profile_id,
                    "partition": case.partition,
                    "execution_status": outcome.execution_status,
                    "usage_status": ("known" if outcome.prompt_tokens is not None else "unknown"),
                },
            )
        case_results.append(
            SummarizationPilotCaseResult(
                case_id=case.case_id,
                partition=case.partition,
                outcomes=(outcomes[0], outcomes[1]),
            )
        )

    frozen_results = tuple(case_results)
    content_sha256 = _collection_digest(
        plan_content_sha256=plan.content_sha256,
        prompt_version=plan.prompt_version,
        rubric_version=plan.rubric_version,
        reserved_tokens=plan.reserved_total_tokens,
        known_actual_tokens=known_actual_tokens,
        unknown_usage_count=unknown_usage_count,
        cases=frozen_results,
    )
    collection = SummarizationPilotCollection(
        plan_content_sha256=plan.content_sha256,
        prompt_version=plan.prompt_version,
        rubric_version=plan.rubric_version,
        reserved_tokens=plan.reserved_total_tokens,
        known_actual_tokens=known_actual_tokens,
        unknown_usage_count=unknown_usage_count,
        cases=frozen_results,
        content_sha256=content_sha256,
    )
    emitter.emit(
        "eval.summarization_pilot.ended",
        span_id=workflow_span,
        payload={
            "ok": True,
            "plan_content_sha256": plan.content_sha256,
            "collection_content_sha256": collection.content_sha256,
            "known_actual_tokens": known_actual_tokens,
            "unknown_usage_count": unknown_usage_count,
        },
    )
    return collection


def _validate_candidate_models(
    plan: SummarizationPilotPlan,
    candidate_models: Mapping[str, Model],
) -> None:
    expected_ids = set(plan.candidate_profile_ids)
    if set(candidate_models) != expected_ids:
        raise SummarizationPilotError("candidate model set does not match the frozen plan")
    for candidate in plan.candidates:
        model = candidate_models[candidate.profile_id]
        identity = identity_of(model)
        if (
            identity is None
            or identity.purpose != "summarization"
            or identity.configuration_fingerprint != candidate.configuration_fingerprint
        ):
            raise SummarizationPilotError("candidate model identity does not match the frozen plan")
        runtime = retry_runtime_of(model)
        if runtime is not None and runtime.policy.enabled and runtime.policy.max_attempts != 1:
            raise SummarizationPilotError("pilot candidate retries must be limited to one attempt")
        if fallback_plan_of(model) is not None:
            raise SummarizationPilotError("pilot collection does not permit provider fallback")


def resolve_summarization_pilot_candidates(
    configuration: ModelConfiguration,
    profile_ids: tuple[str, str],
) -> tuple[SummarizationPilotCandidate, SummarizationPilotCandidate]:
    """Freeze two explicit profile deployments without resolving any credentials."""

    candidates: list[SummarizationPilotCandidate] = []
    for profile_id in profile_ids:
        resolved = configuration.resolve_selected_candidates(
            "summarization",
            ModelSelection(profile_id=profile_id),
        )[0]
        context_window = resolved.profile.context_window_tokens
        max_output = resolved.profile.max_output_tokens
        if context_window is None or max_output is None:
            raise SummarizationPilotError(
                "pilot candidates require explicit context and output token limits"
            )
        candidates.append(
            SummarizationPilotCandidate(
                profile_id=profile_id,
                configuration_fingerprint=resolved.configuration_fingerprint,
                context_window_tokens=context_window,
                max_output_tokens=max_output,
            )
        )
    return (candidates[0], candidates[1])


def _compile_trace_case(
    source_trace_id: str,
    events: Sequence[AgentEvent],
    *,
    max_turns: int,
    holdout_every: int,
) -> SummarizationPilotCase | None:
    if not source_trace_id:
        raise SummarizationPilotError("trace identity must be non-empty")

    starts: dict[str, AgentEvent] = {}
    ends: dict[str, AgentEvent] = {}
    for event in sorted(events, key=lambda item: (item.seq, item.type)):
        if event.trace_id != source_trace_id:
            raise SummarizationPilotError("trace mapping identity does not match its events")
        if event.span_id is None:
            continue
        target = None
        if event.type == EventType.AGENT_TURN_STARTED:
            target = starts
        elif event.type == EventType.AGENT_TURN_ENDED:
            target = ends
        if target is not None:
            if event.span_id in target:
                raise SummarizationPilotError("duplicate agent-turn event for one span")
            target[event.span_id] = event

    turns: list[tuple[int, str, str]] = []
    for span_id in starts.keys() & ends.keys():
        started = starts[span_id]
        ended = ends[span_id]
        user_message = started.payload.get("user_message")
        assistant_output = ended.payload.get("output")
        if (
            ended.payload.get("ok") is True
            and isinstance(user_message, str)
            and user_message.strip()
            and isinstance(assistant_output, str)
            and assistant_output.strip()
        ):
            turns.append((started.seq, user_message, assistant_output))
    if not turns:
        return None

    selected_turns = sorted(turns, key=lambda turn: turn[0])[-max_turns:]
    messages = tuple(
        message
        for _seq, user_message, assistant_output in selected_turns
        for message in (
            SummarizationPilotMessage(role="user", content=user_message),
            SummarizationPilotMessage(role="assistant", content=assistant_output),
        )
    )
    source_group_id = _digest({"trace_id": source_trace_id})
    case_id = _digest(
        {
            "source_group_id": source_group_id,
            "prior_summary": "",
            "messages": [message.model_dump(mode="json") for message in messages],
        }
    )
    partition: Literal["development", "holdout"] = (
        "holdout" if int(source_group_id[:16], 16) % holdout_every == 0 else "development"
    )
    return SummarizationPilotCase(
        case_id=case_id,
        source_group_id=source_group_id,
        partition=partition,
        messages=messages,
    )


def _approval_summary(
    *,
    candidates: tuple[SummarizationPilotCandidate, SummarizationPilotCandidate],
    max_total_tokens: int,
    reserved_total_tokens: int,
    prompt_version: str,
    source_revision: str,
    cases: tuple[SummarizationPilotCase, ...],
) -> SummarizationPilotApprovalSummary:
    return SummarizationPilotApprovalSummary(
        candidate_profile_ids=(candidates[0].profile_id, candidates[1].profile_id),
        candidate_configuration_fingerprints=(
            candidates[0].configuration_fingerprint,
            candidates[1].configuration_fingerprint,
        ),
        max_total_tokens=max_total_tokens,
        reserved_total_tokens=reserved_total_tokens,
        prompt_version=prompt_version,
        case_count=len(cases),
        source_group_count=len({case.source_group_id for case in cases}),
        development_case_count=sum(case.partition == "development" for case in cases),
        holdout_case_count=sum(case.partition == "holdout" for case in cases),
        source_revision=source_revision,
    )


def _plan_digest(
    *,
    candidates: tuple[SummarizationPilotCandidate, SummarizationPilotCandidate],
    max_total_tokens: int,
    reserved_total_tokens: int,
    prompt_version: str,
    source_revision: str,
    cases: tuple[SummarizationPilotCase, ...],
) -> str:
    return _digest(
        {
            "schema_version": "summarization-routing-pilot-plan.v1",
            "consumer": "summarization",
            "data_scope": "successful_local_chat_turns",
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "max_total_tokens": max_total_tokens,
            "reserved_total_tokens": reserved_total_tokens,
            "max_attempts_per_candidate": 1,
            "prompt_version": prompt_version,
            "rubric_id": "summarization_quality",
            "rubric_version": "summarization_quality@v1",
            "source_revision": source_revision,
            "cases": [case.model_dump(mode="json") for case in cases],
        }
    )


def _reserved_total_tokens(
    *,
    cases: tuple[SummarizationPilotCase, ...],
    candidates: tuple[SummarizationPilotCandidate, SummarizationPilotCandidate],
    prompt_text: str,
) -> int:
    """Conservative upper bound: UTF-8 bytes dominate tokenizer fallback pieces."""

    total = 0
    for case in cases:
        input_upper_bound = _case_input_upper_bound(case, prompt_text)
        if any(
            input_upper_bound + candidate.max_output_tokens > candidate.context_window_tokens
            for candidate in candidates
        ):
            raise SummarizationPilotError("pilot case exceeds a candidate context window")
        total += sum(input_upper_bound + candidate.max_output_tokens for candidate in candidates)
    return total


def _case_input_upper_bound(case: SummarizationPilotCase, prompt_text: str) -> int:
    rendered = "\n".join(f"{message.role}：{message.content}" for message in case.messages)
    user_text = f"此前摘要：{case.prior_summary or '（无）'}\n\n新增对话轮次：\n{rendered}"
    # Chat protocols add a small fixed envelope around the two messages. Counting
    # every UTF-8 byte as one token plus a 256-token envelope intentionally
    # over-reserves for both DeepSeek and Qwen tokenizers.
    return len(prompt_text.encode("utf-8")) + len(user_text.encode("utf-8")) + 256


def _collection_digest(
    *,
    plan_content_sha256: str,
    prompt_version: str,
    rubric_version: str,
    reserved_tokens: int,
    known_actual_tokens: int,
    unknown_usage_count: int,
    cases: tuple[SummarizationPilotCaseResult, ...],
) -> str:
    return _digest(
        {
            "schema_version": "summarization-routing-pilot-collection.v1",
            "plan_content_sha256": plan_content_sha256,
            "prompt_version": prompt_version,
            "rubric_version": rubric_version,
            "reserved_tokens": reserved_tokens,
            "known_actual_tokens": known_actual_tokens,
            "unknown_usage_count": unknown_usage_count,
            "cases": [case.model_dump(mode="json") for case in cases],
        }
    )


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
