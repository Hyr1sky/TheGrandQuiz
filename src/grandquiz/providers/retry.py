"""Provider-neutral retry policy and injected execution dependencies."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from grandquiz.providers.failure import ProviderFailure, ProviderFailureCategory

RetryAction = Literal["retry", "stop"]
RetryDecisionReason = Literal[
    "retry_disabled",
    "non_retryable",
    "replay_unsafe",
    "attempt_limit",
    "wait_budget_exhausted",
    "deadline_exhausted",
    "transient_failure",
    "invalid_retry_after",
]

_RETRYABLE_CATEGORIES = frozenset(
    {
        ProviderFailureCategory.CONFLICT,
        ProviderFailureCategory.RATE_LIMITED,
        ProviderFailureCategory.TIMEOUT,
        ProviderFailureCategory.CONNECTION,
        ProviderFailureCategory.SERVER_ERROR,
    }
)


class RetryClock(Protocol):
    def monotonic(self) -> float: ...

    def utc_timestamp(self) -> float: ...


class RetryRandom(Protocol):
    def random(self) -> float: ...


type RetrySleeper = Callable[[float], Awaitable[None]]


class SystemRetryClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utc_timestamp(self) -> float:
        return time.time()


class ProviderRetryPolicy(BaseModel):
    """Bounded local policy; attempt count includes the initial request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    deadline_seconds: float = Field(default=90.0, gt=0, le=600, allow_inf_nan=False)
    base_delay_seconds: float = Field(default=0.5, ge=0, le=60, allow_inf_nan=False)
    max_delay_seconds: float = Field(default=8.0, ge=0, le=120, allow_inf_nan=False)
    max_total_wait_seconds: float = Field(default=30.0, ge=0, le=300, allow_inf_nan=False)
    jitter_ratio: float = Field(default=0.2, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def delay_bounds_are_coherent(self) -> ProviderRetryPolicy:
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max delay must be at least base delay")
        return self

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            {"version": "provider-retry-policy.v1", **self.model_dump()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def decide(
        self,
        failure: ProviderFailure,
        *,
        attempt_index: int,
        elapsed_seconds: float,
        total_wait_seconds: float,
        utc_timestamp: float,
        rng: RetryRandom,
        output_delivered: bool,
    ) -> ProviderRetryDecision:
        if not self.enabled:
            return ProviderRetryDecision(action="stop", reason="retry_disabled")
        if not failure.retryable or failure.category not in _RETRYABLE_CATEGORIES:
            return ProviderRetryDecision(action="stop", reason="non_retryable")
        if output_delivered or failure.response_started or not failure.replay_safe:
            return ProviderRetryDecision(action="stop", reason="replay_unsafe")
        if attempt_index >= self.max_attempts:
            return ProviderRetryDecision(action="stop", reason="attempt_limit")

        local_delay = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** (attempt_index - 1)),
        )
        jitter = 1 - self.jitter_ratio + (2 * self.jitter_ratio * rng.random())
        local_delay *= jitter
        retry_after = failure.retry_after_delay(utc_timestamp)
        delay = max(local_delay, retry_after or 0.0)
        reason: RetryDecisionReason = (
            "invalid_retry_after" if failure.retry_after_invalid else "transient_failure"
        )

        if total_wait_seconds + delay > self.max_total_wait_seconds:
            return ProviderRetryDecision(
                action="stop",
                reason="wait_budget_exhausted",
                delay_seconds=delay,
            )
        remaining = self.deadline_seconds - elapsed_seconds
        if remaining <= 0 or delay >= remaining:
            return ProviderRetryDecision(
                action="stop",
                reason="deadline_exhausted",
                delay_seconds=delay,
            )
        return ProviderRetryDecision(
            action="retry",
            reason=reason,
            delay_seconds=delay,
        )


@dataclass(frozen=True)
class ProviderRetryDecision:
    action: RetryAction
    reason: RetryDecisionReason
    delay_seconds: float | None = None


@dataclass(frozen=True)
class RetryRuntime:
    policy: ProviderRetryPolicy
    clock: RetryClock
    sleeper: RetrySleeper
    rng: RetryRandom

    @classmethod
    def production(
        cls,
        policy: ProviderRetryPolicy,
        *,
        seed: int | None = None,
    ) -> RetryRuntime:
        return cls(
            policy=policy,
            clock=SystemRetryClock(),
            sleeper=asyncio.sleep,
            rng=random.Random(seed),
        )
