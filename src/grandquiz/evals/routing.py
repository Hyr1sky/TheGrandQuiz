"""Provider-neutral, offline evidence for model-routing decisions.

This module evaluates already-recorded candidate outcomes.  It never sends a model
request and deliberately gives callable policies only facts available before a call.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Sequence
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RoutingPartition = Literal["development", "holdout"]
RoutingExecutionStatus = Literal["completed", "provider_error", "runtime_error"]


class RoutingEvaluationError(ValueError):
    """The frozen dataset or a policy decision cannot produce valid evidence."""


class _RoutingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RoutingRequest(_RoutingRecord):
    """Facts a pre-call router may inspect; candidate outcomes are intentionally absent."""

    case_id: str = Field(min_length=1, max_length=256)
    source_group_id: str = Field(min_length=1, max_length=256)
    partition: RoutingPartition
    input_text: str = Field(min_length=1, repr=False)
    features: tuple[tuple[str, str | int | float | bool], ...] = ()

    @model_validator(mode="after")
    def _canonical_features(self) -> Self:
        names = [name for name, _value in self.features]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("routing feature names must be non-empty and unique")
        if tuple(sorted(self.features, key=lambda item: item[0])) != self.features:
            raise ValueError("routing features must use canonical name order")
        if any(isinstance(value, float) and not math.isfinite(value) for _, value in self.features):
            raise ValueError("routing feature values must be finite")
        return self


class RoutingCandidateOutcome(_RoutingRecord):
    """One candidate's precomputed result without collapsing failure into low quality."""

    candidate_id: str = Field(min_length=1, max_length=128)
    execution_status: RoutingExecutionStatus
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    estimated_cost: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    failure_category: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _separate_failure_from_quality(self) -> Self:
        if self.execution_status == "completed" and self.failure_category is not None:
            raise ValueError("completed outcome cannot carry a failure category")
        if self.execution_status != "completed":
            if self.failure_category is None:
                raise ValueError("failed outcome requires a failure category")
            if self.quality_score is not None:
                raise ValueError("failed outcome cannot carry semantic quality")
        return self


class RoutingCase(_RoutingRecord):
    request: RoutingRequest
    outcomes: tuple[RoutingCandidateOutcome, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _unique_candidate_outcomes(self) -> Self:
        candidate_ids = [outcome.candidate_id for outcome in self.outcomes]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("routing case candidate outcomes must be unique")
        return self


class RoutingDataset(_RoutingRecord):
    """Frozen paired outcomes; its digest is stable across case and outcome ordering."""

    schema_version: Literal["routing-dataset.v1"] = "routing-dataset.v1"
    source_kind: str = Field(min_length=1, max_length=128)
    source_revisions: tuple[str, ...] = Field(min_length=1)
    cost_unit: str = Field(min_length=1, max_length=32)
    candidate_ids: tuple[str, ...] = Field(min_length=2)
    cases: tuple[RoutingCase, ...] = Field(min_length=1)

    @field_validator("source_revisions", "candidate_ids")
    @classmethod
    def _canonical_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("routing dataset identities must be non-empty and unique")
        if tuple(sorted(value)) != value:
            raise ValueError("routing dataset identities must use canonical order")
        return value

    @model_validator(mode="after")
    def _paired_cases_and_isolated_groups(self) -> Self:
        case_ids = [case.request.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("routing case ids must be unique")

        expected = set(self.candidate_ids)
        partitions_by_group: dict[str, set[RoutingPartition]] = {}
        for case in self.cases:
            observed = {outcome.candidate_id for outcome in case.outcomes}
            if observed != expected:
                raise ValueError("every routing case must contain the same candidate set")
            partitions_by_group.setdefault(case.request.source_group_id, set()).add(
                case.request.partition
            )
        if any(len(partitions) > 1 for partitions in partitions_by_group.values()):
            raise ValueError("source group cannot cross partitions")
        return self

    @property
    def content_sha256(self) -> str:
        cases: list[dict[str, object]] = []
        for case in sorted(self.cases, key=lambda item: item.request.case_id):
            cases.append(
                {
                    "request": case.request.model_dump(mode="json"),
                    "outcomes": [
                        outcome.model_dump(mode="json")
                        for outcome in sorted(
                            case.outcomes,
                            key=lambda item: item.candidate_id,
                        )
                    ],
                }
            )
        canonical = json.dumps(
            {
                "schema_version": self.schema_version,
                "source_kind": self.source_kind,
                "source_revisions": self.source_revisions,
                "cost_unit": self.cost_unit,
                "candidate_ids": self.candidate_ids,
                "cases": cases,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RoutingPolicy(Protocol):
    """A pre-call decision contract: no output, score, cost, or latency is visible."""

    @property
    def policy_id(self) -> str: ...

    @property
    def policy_fingerprint(self) -> str: ...

    def choose(
        self,
        request: RoutingRequest,
        candidate_ids: tuple[str, ...],
    ) -> str: ...


class FixedCandidatePolicy(_RoutingRecord):
    policy_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)

    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(
            {
                "kind": "fixed-candidate.v1",
                "policy_id": self.policy_id,
                "candidate_id": self.candidate_id,
            }
        )

    def choose(
        self,
        request: RoutingRequest,
        candidate_ids: tuple[str, ...],
    ) -> str:
        del request, candidate_ids
        return self.candidate_id


class SeededRandomPolicy(_RoutingRecord):
    """Replayable uniform baseline without mutable process-global randomness."""

    policy_id: str = Field(min_length=1, max_length=128)
    seed: int

    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(
            {
                "kind": "seeded-random.v1",
                "policy_id": self.policy_id,
                "seed": self.seed,
            }
        )

    def choose(
        self,
        request: RoutingRequest,
        candidate_ids: tuple[str, ...],
    ) -> str:
        material = json.dumps(
            [self.seed, request.case_id, candidate_ids],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(material.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(candidate_ids)
        return candidate_ids[index]


class RoutingDecisionEvidence(_RoutingRecord):
    case_id: str
    candidate_id: str
    execution_status: RoutingExecutionStatus
    quality_score: float | None
    estimated_cost: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float | None
    failure_category: str | None


class RoutingPolicySummary(_RoutingRecord):
    policy_id: str
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_only: bool
    sample_count: int = Field(ge=1)
    completed_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    quality_observation_count: int = Field(ge=0)
    mean_quality: float | None
    known_cost_count: int = Field(ge=0)
    total_known_cost: float | None
    mean_known_cost: float | None
    known_prompt_token_count: int = Field(ge=0)
    total_known_prompt_tokens: int | None = Field(ge=0)
    known_completion_token_count: int = Field(ge=0)
    total_known_completion_tokens: int | None = Field(ge=0)
    latency_observation_count: int = Field(ge=0)
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    selection_counts: tuple[tuple[str, int], ...]
    failure_counts: tuple[tuple[str, int], ...]
    decisions: tuple[RoutingDecisionEvidence, ...]


class RoutingEvaluationReport(_RoutingRecord):
    schema_version: Literal["routing-evaluation-report.v1"] = "routing-evaluation-report.v1"
    dataset_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_source_kind: str
    dataset_source_revisions: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    partition: RoutingPartition
    cost_unit: str
    policy_summaries: tuple[RoutingPolicySummary, ...] = Field(min_length=1)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return round(ordered[rank - 1], 6)


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_summary(
    policy_id: str,
    *,
    policy_fingerprint: str,
    analysis_only: bool,
    decisions: tuple[RoutingDecisionEvidence, ...],
) -> RoutingPolicySummary:
    quality = [item.quality_score for item in decisions if item.quality_score is not None]
    costs = [item.estimated_cost for item in decisions if item.estimated_cost is not None]
    prompt_tokens = [item.prompt_tokens for item in decisions if item.prompt_tokens is not None]
    completion_tokens = [
        item.completion_tokens for item in decisions if item.completion_tokens is not None
    ]
    latencies = [item.latency_ms for item in decisions if item.latency_ms is not None]
    failures = Counter(
        item.failure_category for item in decisions if item.failure_category is not None
    )
    selections = Counter(item.candidate_id for item in decisions)
    total_cost = None if not costs else round(sum(costs), 12)
    return RoutingPolicySummary(
        policy_id=policy_id,
        policy_fingerprint=policy_fingerprint,
        analysis_only=analysis_only,
        sample_count=len(decisions),
        completed_count=sum(item.execution_status == "completed" for item in decisions),
        failure_count=sum(item.execution_status != "completed" for item in decisions),
        quality_observation_count=len(quality),
        mean_quality=None if not quality else round(sum(quality) / len(quality), 12),
        known_cost_count=len(costs),
        total_known_cost=total_cost,
        mean_known_cost=None if total_cost is None else round(total_cost / len(costs), 12),
        known_prompt_token_count=len(prompt_tokens),
        total_known_prompt_tokens=None if not prompt_tokens else sum(prompt_tokens),
        known_completion_token_count=len(completion_tokens),
        total_known_completion_tokens=None if not completion_tokens else sum(completion_tokens),
        latency_observation_count=len(latencies),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        selection_counts=tuple(sorted(selections.items())),
        failure_counts=tuple(sorted((name, count) for name, count in failures.items())),
        decisions=decisions,
    )


def _decision(case: RoutingCase, candidate_id: str) -> RoutingDecisionEvidence:
    outcome = next(outcome for outcome in case.outcomes if outcome.candidate_id == candidate_id)
    return RoutingDecisionEvidence(
        case_id=case.request.case_id,
        candidate_id=candidate_id,
        execution_status=outcome.execution_status,
        quality_score=outcome.quality_score,
        estimated_cost=outcome.estimated_cost,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        latency_ms=outcome.latency_ms,
        failure_category=outcome.failure_category,
    )


def _quality_oracle_choice(case: RoutingCase, candidate_ids: tuple[str, ...]) -> str:
    outcomes = {outcome.candidate_id: outcome for outcome in case.outcomes}
    scored = [
        candidate_id
        for candidate_id in candidate_ids
        if outcomes[candidate_id].execution_status == "completed"
        and outcomes[candidate_id].quality_score is not None
    ]
    if not scored:
        return candidate_ids[0]
    return max(scored, key=lambda candidate_id: outcomes[candidate_id].quality_score or 0.0)


def evaluate_routing_policies(
    dataset: RoutingDataset,
    *,
    partition: RoutingPartition,
    policies: Sequence[RoutingPolicy],
    include_quality_oracle: bool = False,
) -> RoutingEvaluationReport:
    """Compare pre-call policies on one frozen partition; never authorise production routing."""

    cases = tuple(
        sorted(
            (case for case in dataset.cases if case.request.partition == partition),
            key=lambda item: item.request.case_id,
        )
    )
    if not cases:
        raise RoutingEvaluationError("routing partition contains no cases")
    policy_ids = [policy.policy_id for policy in policies]
    if len(policy_ids) != len(set(policy_ids)) or any(
        policy_id.startswith("analysis:") for policy_id in policy_ids
    ):
        raise RoutingEvaluationError("routing policy identities must be unique and non-reserved")

    summaries: list[RoutingPolicySummary] = []
    for policy in policies:
        decisions: list[RoutingDecisionEvidence] = []
        for case in cases:
            candidate_id = policy.choose(case.request, dataset.candidate_ids)
            if candidate_id not in dataset.candidate_ids:
                raise RoutingEvaluationError("routing policy selected an unknown candidate")
            decisions.append(_decision(case, candidate_id))
        summaries.append(
            _build_summary(
                policy.policy_id,
                policy_fingerprint=policy.policy_fingerprint,
                analysis_only=False,
                decisions=tuple(decisions),
            )
        )

    if include_quality_oracle:
        oracle_decisions = tuple(
            _decision(case, _quality_oracle_choice(case, dataset.candidate_ids)) for case in cases
        )
        summaries.append(
            _build_summary(
                "analysis:quality-oracle",
                policy_fingerprint=_fingerprint({"kind": "quality-oracle.v1"}),
                analysis_only=True,
                decisions=oracle_decisions,
            )
        )
    if not summaries:
        raise RoutingEvaluationError("at least one routing policy or analysis baseline is required")
    return RoutingEvaluationReport(
        dataset_content_sha256=dataset.content_sha256,
        dataset_source_kind=dataset.source_kind,
        dataset_source_revisions=dataset.source_revisions,
        candidate_ids=dataset.candidate_ids,
        partition=partition,
        cost_unit=dataset.cost_unit,
        policy_summaries=tuple(summaries),
    )
