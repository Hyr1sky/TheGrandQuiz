"""Provider-neutral speech-recognition Interface and stable result/error contracts."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grandquiz.domain.learning.recognition_lexicon import TranscriptionHints

MAX_AUDIO_BYTES = 7_000_000

SpeechRecognitionErrorCode = Literal[
    "invalid_audio",
    "unsupported_media",
    "payload_too_large",
    "provider_auth",
    "provider_rate_limited",
    "provider_timeout",
    "provider_unavailable",
]


class TranscriptionRequest(BaseModel):
    """One complete audio artifact and provider-neutral recognition hints."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    audio_bytes: bytes
    mime_type: str = Field(min_length=1)
    hints: TranscriptionHints
    material_hints_enabled: bool = False
    locale_hints: tuple[str, ...] = ("zh", "en")
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)

    @field_validator("audio_bytes")
    @classmethod
    def audio_is_bounded(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("audio_bytes 不能为空")
        if len(value) > MAX_AUDIO_BYTES:
            raise ValueError(f"audio_bytes 不能超过 {MAX_AUDIO_BYTES} bytes")
        return value


class TranscriptionResult(BaseModel):
    """Stable fields shared by product consumers; Provider raw payload stays private."""

    model_config = ConfigDict(frozen=True)

    transcript: str = Field(min_length=1)
    provider_request_id: str | None = None
    provider_audio_duration_ms: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)


class SpeechRecognitionError(RuntimeError):
    """Safe external failure classification crossing the Provider seam."""

    def __init__(
        self,
        code: SpeechRecognitionErrorCode,
        reason: str,
        *,
        retryable: bool,
        provider_status: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        self.code = code
        self.reason = reason
        self.retryable = retryable
        self.provider_status = provider_status
        self.provider_request_id = provider_request_id
        super().__init__(reason)


class SpeechRecognitionProvider(Protocol):
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...
