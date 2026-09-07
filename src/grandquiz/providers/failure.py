"""Provider-neutral failures with safe, structured diagnostic fields."""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Literal


class ProviderFailureCategory(enum.StrEnum):
    """Stable categories shared by all LLM provider adapters."""

    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


PublicProviderReasonCode = Literal[
    "provider_quota_exhausted",
    "provider_authentication_failed",
    "provider_permission_denied",
    "provider_request_invalid",
    "provider_model_not_found",
    "provider_conflict",
    "provider_rate_limited",
    "provider_timeout",
    "provider_unavailable",
    "provider_error",
]

_PUBLIC_REASON_BY_CATEGORY: Mapping[ProviderFailureCategory, PublicProviderReasonCode] = {
    ProviderFailureCategory.INVALID_REQUEST: "provider_request_invalid",
    ProviderFailureCategory.AUTHENTICATION: "provider_authentication_failed",
    ProviderFailureCategory.PERMISSION_DENIED: "provider_permission_denied",
    ProviderFailureCategory.NOT_FOUND: "provider_model_not_found",
    ProviderFailureCategory.CONFLICT: "provider_conflict",
    ProviderFailureCategory.QUOTA_EXHAUSTED: "provider_quota_exhausted",
    ProviderFailureCategory.RATE_LIMITED: "provider_rate_limited",
    ProviderFailureCategory.TIMEOUT: "provider_timeout",
    ProviderFailureCategory.CONNECTION: "provider_unavailable",
    ProviderFailureCategory.SERVER_ERROR: "provider_unavailable",
    ProviderFailureCategory.UNKNOWN: "provider_error",
}

_SAFE_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RETRY_AFTER_SECONDS = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


@dataclass(frozen=True, repr=False)
class RetryAfter:
    seconds: float | None = None
    utc_timestamp: float | None = None
    invalid: bool = False

    def __repr__(self) -> str:
        return "RetryAfter(parsed)"


def parse_retry_after(value: object) -> RetryAfter:
    """Parse Retry-After without retaining arbitrary upstream header text."""

    if not isinstance(value, str) or not value or len(value) > 256:
        return RetryAfter(invalid=True)
    normalized = value.strip()
    if _RETRY_AFTER_SECONDS.fullmatch(normalized):
        try:
            seconds = float(normalized)
        except ValueError:
            return RetryAfter(invalid=True)
        if math.isfinite(seconds):
            return RetryAfter(seconds=seconds)
        return RetryAfter(invalid=True)
    try:
        parsed = parsedate_to_datetime(normalized)
        if parsed.tzinfo is None:
            return RetryAfter(invalid=True)
        timestamp = parsed.timestamp()
    except (OverflowError, TypeError, ValueError):
        return RetryAfter(invalid=True)
    return (
        RetryAfter(utc_timestamp=timestamp)
        if math.isfinite(timestamp)
        else RetryAfter(invalid=True)
    )


def safe_provider_code(value: object) -> str | None:
    """Allow only short identifier-like vendor codes into structured traces."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if _SAFE_CODE.fullmatch(normalized) else None


def _safe_retry_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) and normalized >= 0 else None


class ProviderFailure(RuntimeError):
    """A provider failure stripped of arbitrary upstream response text.

    The original SDK exception remains available through ``__cause__`` for local
    logging. ``str()`` and ``repr()`` intentionally contain only stable safe fields,
    because both currently enter the internal event stream.
    """

    def __init__(
        self,
        *,
        category: ProviderFailureCategory,
        retryable: bool,
        status_code: int | None = None,
        provider_code: str | None = None,
        retry_after_seconds: float | None = None,
        retry_after_at: float | None = None,
        retry_after_invalid: bool = False,
        response_started: bool = False,
        replay_safe: bool | None = None,
    ) -> None:
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.provider_code = safe_provider_code(provider_code)
        safe_seconds = _safe_retry_value(retry_after_seconds)
        safe_at = _safe_retry_value(retry_after_at)
        invalid_retry_fact = (
            (retry_after_seconds is not None and safe_seconds is None)
            or (retry_after_at is not None and safe_at is None)
            or (safe_seconds is not None and safe_at is not None)
        )
        if safe_seconds is not None and safe_at is not None:
            safe_seconds = None
            safe_at = None
        self.retry_after_seconds = safe_seconds
        self.retry_after_at = safe_at
        self.retry_after_invalid = retry_after_invalid or invalid_retry_fact
        self.response_started = response_started
        self.replay_safe = (
            False if response_started else True if replay_safe is None else replay_safe
        )
        super().__init__(self._safe_message())

    @property
    def public_reason_code(self) -> PublicProviderReasonCode:
        return _PUBLIC_REASON_BY_CATEGORY[self.category]

    def _safe_message(self) -> str:
        fields = [f"category={self.category.value}", f"retryable={str(self.retryable).lower()}"]
        if self.status_code is not None:
            fields.append(f"status={self.status_code}")
        if self.provider_code is not None:
            fields.append(f"provider_code={self.provider_code}")
        return "provider request failed (" + ", ".join(fields) + ")"

    def retry_after_delay(self, utc_timestamp: float) -> float | None:
        if self.retry_after_seconds is not None:
            return self.retry_after_seconds
        if self.retry_after_at is not None:
            return max(0.0, self.retry_after_at - utc_timestamp)
        return None


def provider_failure_payload(exc: BaseException) -> dict[str, object]:
    """Return the allowlisted event fields for a typed provider failure."""

    if not isinstance(exc, ProviderFailure):
        return {}
    payload: dict[str, object] = {
        "provider_failure_category": exc.category.value,
        "provider_failure_code": exc.public_reason_code,
        "provider_retryable": exc.retryable,
    }
    if exc.response_started:
        payload["provider_response_started"] = True
    if not exc.replay_safe:
        payload["provider_replay_safe"] = False
    if exc.status_code is not None:
        payload["provider_status_code"] = exc.status_code
    if exc.provider_code is not None:
        payload["provider_code"] = exc.provider_code
    if exc.retry_after_seconds is not None:
        payload["provider_retry_after_seconds"] = exc.retry_after_seconds
    if exc.retry_after_at is not None:
        payload["provider_retry_after_at"] = exc.retry_after_at
    if exc.retry_after_invalid:
        payload["provider_retry_after_invalid"] = True
    return payload
