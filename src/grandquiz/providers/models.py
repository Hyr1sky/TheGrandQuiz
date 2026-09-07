"""Frozen purpose bindings and transport ownership, independent of business vocabulary."""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    Model,
    ProviderStreamEvent,
    StreamingModel,
    TextDelta,
    ToolSpec,
)
from grandquiz.providers.llm import ChatModelConfig, OpenAIChatModel
from grandquiz.providers.profiles import ModelConfiguration, ModelConfigurationError, ModelIdentity


@dataclass(frozen=True)
class BoundModel:
    inner: Model
    identity: ModelIdentity

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return await self.inner.complete(messages, tools=tools)


@dataclass(frozen=True)
class _BoundStreamingModel(BoundModel):
    inner: StreamingModel

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        async for event in self.inner.stream_complete(messages, tools=tools):
            yield event


@dataclass(frozen=True)
class CompletionAsStreamModel:
    """Explicit compatibility adapter for callers that require display deltas."""

    inner: Model

    @property
    def identity(self) -> ModelIdentity | None:
        return identity_of(self.inner)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return await self.inner.complete(messages, tools=tools)

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        completion = await self.inner.complete(messages, tools=tools)
        if completion.text:
            yield TextDelta(text=completion.text)
        yield CompletionFinished(completion=completion)


def as_streaming_model(model: Model) -> StreamingModel:
    """Keep native streaming or opt in explicitly to completion-as-stream compatibility."""
    return model if isinstance(model, StreamingModel) else CompletionAsStreamModel(model)


def with_identity(model: Model, identity: ModelIdentity) -> BoundModel:
    if isinstance(model, StreamingModel):
        return _BoundStreamingModel(model, identity)
    return BoundModel(model, identity)


@dataclass(frozen=True)
class ModelBindings:
    """One fixed model per registered purpose; never discovers or switches suppliers."""

    models: tuple[tuple[str, Model], ...]

    def for_purpose(self, purpose: str) -> Model:
        for name, model in self.models:
            if name == purpose:
                return model
        raise ModelConfigurationError("unknown_purpose")

    def identity_for(self, purpose: str) -> ModelIdentity | None:
        return identity_of(self.for_purpose(purpose))

    def model_identities(self) -> tuple[ModelIdentity, ...]:
        return tuple(
            identity for _, model in self.models if (identity := identity_of(model)) is not None
        )


@runtime_checkable
class PurposeModels(Protocol):
    def for_purpose(self, purpose: str) -> Model: ...


type ModelSource = Model | PurposeModels


@runtime_checkable
class ModelIdentitySource(Protocol):
    def model_identities(self) -> tuple[ModelIdentity, ...]: ...


def bind_model(source: ModelSource, purpose: str) -> Model:
    """At composition/consumer entry, bind a purpose or accept an injected fixed model."""
    if isinstance(source, PurposeModels):
        return source.for_purpose(purpose)
    identity = identity_of(source)
    if identity is not None and identity.purpose != purpose:
        raise ModelConfigurationError("invalid_configuration")
    return source


def identity_of(model: Model) -> ModelIdentity | None:
    identity = getattr(model, "identity", None)
    return identity if isinstance(identity, ModelIdentity) else None


def identities_of(source: ModelSource) -> tuple[ModelIdentity, ...]:
    if isinstance(source, ModelIdentitySource):
        return source.model_identities()
    if isinstance(source, PurposeModels):
        return ()
    identity = identity_of(source)
    return () if identity is None else (identity,)


class ModelRuntime:
    """Owner of transports; borrowed purpose bindings do not close shared connections."""

    def __init__(self, bindings: ModelBindings, owned: Sequence[OpenAIChatModel]) -> None:
        self.bindings = bindings
        self._owned = tuple(owned)

    @classmethod
    def from_configuration(
        cls,
        config: ModelConfiguration,
        *,
        environment: Mapping[str, str],
    ) -> "ModelRuntime":
        credentials: dict[str, str] = {}
        for binding in config.bindings:
            reference = binding.connection.api_key_env
            key = environment.get(reference, "")
            if not key.strip():
                raise ModelConfigurationError("missing_credential")
            credentials[reference] = key
        transports: dict[tuple[str, str], OpenAIChatModel] = {}
        models: list[tuple[str, Model]] = []
        for binding in config.bindings:
            reference = binding.connection.api_key_env
            transport_key = (reference, binding.configuration_fingerprint)
            if transport_key not in transports:
                profile = binding.profile
                transports[transport_key] = OpenAIChatModel(
                    ChatModelConfig(
                        api_key=credentials[reference],
                        base_url=binding.connection.base_url,
                        model=profile.model,
                        timeout_seconds=profile.timeout_seconds,
                        api_dialect=profile.api_dialect,
                        thinking_mode=profile.thinking_mode,
                        reasoning_effort=profile.reasoning_effort,
                        only_provider=profile.only_provider,
                    )
                )
            models.append(
                (
                    binding.purpose,
                    with_identity(
                        transports[transport_key],
                        config.identity_for(binding.purpose),
                    ),
                )
            )
        return cls(ModelBindings(tuple(models)), tuple(transports.values()))

    async def aclose(self) -> None:
        for model in self._owned:
            await model.aclose()
