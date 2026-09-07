"""V4 replay for resolved models, including deterministic failure sequences."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grandquiz.providers.base import Completion, Message, Model, ToolSpec
from grandquiz.providers.failure import ProviderFailure, ProviderFailureCategory
from grandquiz.providers.models import identity_of, retry_runtime_of
from grandquiz.providers.profiles import ModelConfigurationError, ModelIdentity
from grandquiz.providers.replay import ReplayMiss
from grandquiz.providers.retry import RetryRuntime


def model_request_key(
    identity: ModelIdentity,
    messages: Sequence[Message],
    tools: Sequence[ToolSpec] | None,
) -> str:
    """Keep the v3 request fingerprint stable while the outcome schema evolves."""

    envelope = {
        "fingerprint_version": 3,
        "identity": identity.model_dump(),
        "messages": [message.model_dump(exclude_none=True) for message in messages],
        "tools": sorted(
            [
                {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
                for tool in tools or ()
            ],
            key=lambda tool: str(tool["name"]),
        ),
    }
    raw = json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class _RecordedSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["success"] = "success"
    completion: Completion


class _RecordedFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["failure"] = "failure"
    category: ProviderFailureCategory
    retryable: bool
    status_code: int | None = None
    provider_code: str | None = None
    retry_after_seconds: float | None = None
    retry_after_at: float | None = None
    retry_after_invalid: bool = False
    response_started: bool = False
    replay_safe: bool = True

    @classmethod
    def from_failure(cls, failure: ProviderFailure) -> _RecordedFailure:
        return cls(
            category=failure.category,
            retryable=failure.retryable,
            status_code=failure.status_code,
            provider_code=failure.provider_code,
            retry_after_seconds=failure.retry_after_seconds,
            retry_after_at=failure.retry_after_at,
            retry_after_invalid=failure.retry_after_invalid,
            response_started=failure.response_started,
            replay_safe=failure.replay_safe,
        )

    def to_failure(self) -> ProviderFailure:
        return ProviderFailure(
            category=self.category,
            retryable=self.retryable,
            status_code=self.status_code,
            provider_code=self.provider_code,
            retry_after_seconds=self.retry_after_seconds,
            retry_after_at=self.retry_after_at,
            retry_after_invalid=self.retry_after_invalid,
            response_started=self.response_started,
            replay_safe=self.replay_safe,
        )


type _RecordedOutcome = Annotated[
    _RecordedSuccess | _RecordedFailure,
    Field(discriminator="kind"),
]


class _RecordedCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: ModelIdentity
    outcomes: list[_RecordedOutcome]


class _CassetteDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["model-cassette.v4"]
    calls: dict[str, _RecordedCall] = Field(default_factory=dict)


class _RecordedCallV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: ModelIdentity
    completions: list[Completion]


class _CassetteDocumentV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["model-cassette.v3"]
    calls: dict[str, _RecordedCallV3] = Field(default_factory=dict)


class ModelCassette:
    def __init__(self) -> None:
        self._document = _CassetteDocument(schema_version="model-cassette.v4")
        self._positions: dict[str, int] = {}

    def record(self, key: str, identity: ModelIdentity, completion: Completion) -> None:
        call = self._document.calls.setdefault(key, _RecordedCall(identity=identity, outcomes=[]))
        call.outcomes.append(_RecordedSuccess(completion=completion.model_copy(deep=True)))

    def record_failure(
        self,
        key: str,
        identity: ModelIdentity,
        failure: ProviderFailure,
    ) -> None:
        call = self._document.calls.setdefault(key, _RecordedCall(identity=identity, outcomes=[]))
        call.outcomes.append(_RecordedFailure.from_failure(failure))

    def next(self, key: str) -> Completion:
        call = self._document.calls.get(key)
        position = self._positions.get(key, 0)
        if call is None or position >= len(call.outcomes):
            raise ReplayMiss("v4 回放未命中或序列已耗尽")
        self._positions[key] = position + 1
        outcome = call.outcomes[position]
        if isinstance(outcome, _RecordedFailure):
            raise outcome.to_failure()
        return outcome.completion.model_copy(deep=True)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self._document.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ModelCassette:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            raise ReplayMiss("无法读取模型录制") from None
        try:
            document = _CassetteDocument.model_validate_json(raw)
        except (ValueError, ValidationError):
            try:
                legacy = _CassetteDocumentV3.model_validate_json(raw)
            except (ValueError, ValidationError):
                raise ReplayMiss("无法读取模型录制；旧录制必须使用显式 legacy reader") from None
            document = _CassetteDocument(
                schema_version="model-cassette.v4",
                calls={
                    key: _RecordedCall(
                        identity=call.identity,
                        outcomes=[
                            _RecordedSuccess(completion=completion)
                            for completion in call.completions
                        ],
                    )
                    for key, call in legacy.calls.items()
                },
            )
        cassette = cls()
        cassette._document = document
        return cassette


class RecordingModel:
    """Record each transport outcome without owning retry or model selection."""

    def __init__(
        self,
        inner: Model,
        cassette: ModelCassette,
        *,
        checkpoint_path: Path | None = None,
    ) -> None:
        identity = identity_of(inner)
        if identity is None:
            raise ModelConfigurationError("invalid_configuration")
        self.identity = identity
        self.retry_runtime = retry_runtime_of(inner)
        self._inner = inner
        self._cassette = cassette
        self._checkpoint_path = checkpoint_path

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        key = model_request_key(self.identity, messages, tools)
        try:
            completion = await self._inner.complete(messages, tools=tools)
        except ProviderFailure as exc:
            self._cassette.record_failure(key, self.identity, exc)
            self._checkpoint()
            raise
        self._cassette.record(key, self.identity, completion)
        self._checkpoint()
        return completion

    def _checkpoint(self) -> None:
        if self._checkpoint_path is None:
            return
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._cassette.save(self._checkpoint_path)


class ReplayModel:
    def __init__(
        self,
        cassette: ModelCassette,
        identity: ModelIdentity,
        *,
        retry_runtime: RetryRuntime | None = None,
    ) -> None:
        self.identity = identity
        self.retry_runtime = retry_runtime
        self._cassette = cassette

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return self._cassette.next(model_request_key(self.identity, messages, tools))
