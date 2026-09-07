"""V3 replay for resolved models, deliberately separate from role-based legacy readers."""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grandquiz.providers.base import Completion, Message, Model, ToolSpec
from grandquiz.providers.models import identity_of
from grandquiz.providers.profiles import ModelConfigurationError, ModelIdentity
from grandquiz.providers.replay import ReplayMiss


def model_request_key(
    identity: ModelIdentity,
    messages: Sequence[Message],
    tools: Sequence[ToolSpec] | None,
) -> str:
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


class _RecordedCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: ModelIdentity
    completions: list[Completion]


class _CassetteDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["model-cassette.v3"]
    calls: dict[str, _RecordedCall] = Field(default_factory=dict)


class ModelCassette:
    def __init__(self) -> None:
        self._document = _CassetteDocument(schema_version="model-cassette.v3")
        self._positions: dict[str, int] = {}

    def record(self, key: str, identity: ModelIdentity, completion: Completion) -> None:
        call = self._document.calls.setdefault(
            key, _RecordedCall(identity=identity, completions=[])
        )
        call.completions.append(completion.model_copy(deep=True))

    def next(self, key: str) -> Completion:
        call = self._document.calls.get(key)
        position = self._positions.get(key, 0)
        if call is None or position >= len(call.completions):
            raise ReplayMiss("v3 回放未命中或序列已耗尽")
        self._positions[key] = position + 1
        return call.completions[position].model_copy(deep=True)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self._document.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ModelCassette":
        try:
            document = _CassetteDocument.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError):
            raise ReplayMiss("无法读取 v3 模型录制；旧录制必须使用显式 legacy reader") from None
        cassette = cls()
        cassette._document = document
        return cassette


class RecordingModel:
    """Record exactly the bound call's identity, without discovering or selecting a model."""

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
        completion = await self._inner.complete(messages, tools=tools)
        self._cassette.record(key, self.identity, completion)
        if self._checkpoint_path is not None:
            self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._cassette.save(self._checkpoint_path)
        return completion


class ReplayModel:
    def __init__(self, cassette: ModelCassette, identity: ModelIdentity) -> None:
        self.identity = identity
        self._cassette = cassette

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return self._cassette.next(model_request_key(self.identity, messages, tools))
