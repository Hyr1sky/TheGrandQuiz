"""Provider-neutral policy for explicit, serial availability fallback."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.providers.failure import ProviderFailure, ProviderFailureCategory

FallbackAction = Literal["switch", "stop"]
FallbackDecisionReason = Literal[
    "fallback_disabled",
    "failure_not_allowed",
    "replay_unsafe",
    "attempt_limit",
    "deadline_exhausted",
    "candidates_exhausted",
    "candidate_ineligible",
    "candidate_available",
]

_FALLBACK_CATEGORIES = frozenset(
    {
        ProviderFailureCategory.RATE_LIMITED,
        ProviderFailureCategory.TIMEOUT,
        ProviderFailureCategory.CONNECTION,
        ProviderFailureCategory.SERVER_ERROR,
    }
)


class ProviderFallbackPolicy(BaseModel):
    """Opt-in fallback policy; total attempts/deadline remain owned by retry."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: bool = False
    max_attempts_per_candidate: int = Field(default=1, ge=1, le=10)

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            {"version": "provider-fallback-policy.v1", **self.model_dump()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def allows_failure(self, failure: ProviderFailure) -> bool:
        return failure.retryable and failure.category in _FALLBACK_CATEGORIES

    def decide(
        self,
        failure: ProviderFailure,
        *,
        total_attempt_index: int,
        global_attempt_limit: int,
        elapsed_seconds: float,
        deadline_seconds: float,
        output_delivered: bool,
        next_candidate: int | None,
        skipped_ineligible: bool,
    ) -> ProviderFallbackDecision:
        if not self.enabled:
            return ProviderFallbackDecision(action="stop", reason="fallback_disabled")
        if not self.allows_failure(failure):
            return ProviderFallbackDecision(action="stop", reason="failure_not_allowed")
        if output_delivered or failure.response_started or not failure.replay_safe:
            return ProviderFallbackDecision(action="stop", reason="replay_unsafe")
        if total_attempt_index >= global_attempt_limit:
            return ProviderFallbackDecision(action="stop", reason="attempt_limit")
        if elapsed_seconds >= deadline_seconds:
            return ProviderFallbackDecision(action="stop", reason="deadline_exhausted")
        if next_candidate is None:
            return ProviderFallbackDecision(
                action="stop",
                reason="candidate_ineligible" if skipped_ineligible else "candidates_exhausted",
            )
        return ProviderFallbackDecision(
            action="switch",
            reason="candidate_available",
            next_candidate=next_candidate,
        )


@dataclass(frozen=True)
class ProviderFallbackDecision:
    action: FallbackAction
    reason: FallbackDecisionReason
    next_candidate: int | None = None
