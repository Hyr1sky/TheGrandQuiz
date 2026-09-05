"""Provider-neutral failures with safe, structured diagnostic fields."""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
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


def safe_provider_code(value: object) -> str | None:
    """Allow only short identifier-like vendor codes into structured traces."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if _SAFE_CODE.fullmatch(normalized) else None


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
    ) -> None:
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.provider_code = safe_provider_code(provider_code)
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


def provider_failure_payload(exc: BaseException) -> dict[str, object]:
    """Return the allowlisted event fields for a typed provider failure."""

    if not isinstance(exc, ProviderFailure):
        return {}
    payload: dict[str, object] = {
        "provider_failure_category": exc.category.value,
        "provider_failure_code": exc.public_reason_code,
        "provider_retryable": exc.retryable,
    }
    if exc.status_code is not None:
        payload["provider_status_code"] = exc.status_code
    if exc.provider_code is not None:
        payload["provider_code"] = exc.provider_code
    return payload
