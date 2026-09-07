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
from grandquiz.providers.profiles import (
    ModelConfiguration,
    ModelConfigurationError,
    ModelIdentity,
    ModelProfile,
    ModelRequestRequirements,
    ModelSelection,
    ModelSelectionError,
    ModelSelectionOption,
    ResolvedProfile,
)
from grandquiz.providers.retry import RetryRuntime


@dataclass(frozen=True)
class BoundModel:
    inner: Model
    identity: ModelIdentity
    retry_runtime: RetryRuntime | None = None

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

    @property
    def retry_runtime(self) -> RetryRuntime | None:
        return retry_runtime_of(self.inner)

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


def with_identity(
    model: Model,
    identity: ModelIdentity,
    *,
    retry_runtime: RetryRuntime | None = None,
) -> BoundModel:
    if isinstance(model, StreamingModel):
        return _BoundStreamingModel(model, identity, retry_runtime)
    return BoundModel(model, identity, retry_runtime)


@dataclass(frozen=True)
class ModelBindings:
    """One fixed model per registered purpose; never discovers or switches suppliers."""

    models: tuple[tuple[str, Model], ...]
    configuration: ModelConfiguration | None = None
    profile_models: tuple[tuple[str, Model], ...] = ()
    retry_runtime: RetryRuntime | None = None

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

    def select_for_purpose(
        self,
        purpose: str,
        selection: ModelSelection,
        *,
        requirements: ModelRequestRequirements | None = None,
    ) -> Model:
        configuration = self.configuration
        if configuration is None:
            raise ModelSelectionError(
                "unknown_preset" if selection.preset is not None else "unknown_profile"
            )
        resolved = configuration.resolve_selection(
            purpose,
            selection,
            requirements=requirements,
        )
        model = next(
            (
                model
                for profile_id, model in self.profile_models
                if profile_id == resolved.profile_id
            ),
            None,
        )
        if model is None:
            raise ModelSelectionError("unknown_profile")
        return with_identity(
            model,
            configuration.identity_for_resolved(resolved),
            retry_runtime=self.retry_runtime,
        )

    def selection_options(self, purpose: str) -> tuple[ModelSelectionOption, ...]:
        configuration = self.configuration
        if configuration is None:
            return ()
        configuration.resolve(purpose)
        return tuple(
            ModelSelectionOption(
                profile_id=profile_id,
                presets=tuple(
                    name for name, target in configuration.presets if target == profile_id
                ),
                capabilities=profile.capabilities,
            )
            for profile_id, profile, _connection in configuration.profile_catalog
        )


@runtime_checkable
class PurposeModels(Protocol):
    def for_purpose(self, purpose: str) -> Model: ...


type ModelSource = Model | PurposeModels


@runtime_checkable
class SelectableModels(Protocol):
    def select_for_purpose(
        self,
        purpose: str,
        selection: ModelSelection,
        *,
        requirements: ModelRequestRequirements | None = None,
    ) -> Model: ...

    def selection_options(self, purpose: str) -> tuple[ModelSelectionOption, ...]: ...


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


def select_model(
    source: ModelSource,
    purpose: str,
    selection: ModelSelection | None,
    *,
    requirements: ModelRequestRequirements | None = None,
) -> Model:
    """Resolve and freeze one model without exposing catalog or capability logic to callers."""
    if selection is None:
        return bind_model(source, purpose)
    if not isinstance(source, SelectableModels):
        raise ModelSelectionError(
            "unknown_preset" if selection.preset is not None else "unknown_profile"
        )
    return source.select_for_purpose(
        purpose,
        selection,
        requirements=requirements,
    )


def selection_options_of(
    source: ModelSource,
    purpose: str,
) -> tuple[ModelSelectionOption, ...]:
    if not isinstance(source, SelectableModels):
        return ()
    return source.selection_options(purpose)


def identity_of(model: Model) -> ModelIdentity | None:
    identity = getattr(model, "identity", None)
    return identity if isinstance(identity, ModelIdentity) else None


def retry_runtime_of(model: Model) -> RetryRuntime | None:
    runtime = getattr(model, "retry_runtime", None)
    return runtime if isinstance(runtime, RetryRuntime) else None


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
        retry_runtime: RetryRuntime | None = None,
        retry_seed: int | None = None,
    ) -> "ModelRuntime":
        active_retry_runtime = retry_runtime or RetryRuntime.production(
            config.retry_policy,
            seed=retry_seed,
        )
        credentials: dict[str, str] = {}
        configured_profiles = tuple(
            (profile, connection) for _, profile, connection in config.profile_catalog
        ) or tuple((binding.profile, binding.connection) for binding in config.bindings)
        for _profile, connection in configured_profiles:
            reference = connection.api_key_env
            key = environment.get(reference, "")
            if not key.strip():
                raise ModelConfigurationError("missing_credential")
            credentials[reference] = key
        transports: dict[tuple[str, str], OpenAIChatModel] = {}

        def transport_for(
            *,
            reference: str,
            fingerprint: str,
            base_url: str,
            profile: ModelProfile,
        ) -> OpenAIChatModel:
            transport_key = (reference, fingerprint)
            if transport_key not in transports:
                transports[transport_key] = OpenAIChatModel(
                    ChatModelConfig(
                        api_key=credentials[reference],
                        base_url=base_url,
                        model=profile.model,
                        timeout_seconds=profile.timeout_seconds,
                        api_dialect=profile.api_dialect,
                        thinking_mode=profile.thinking_mode,
                        reasoning_effort=profile.reasoning_effort,
                        only_provider=profile.only_provider,
                    )
                )
            return transports[transport_key]

        bound_profiles = {
            binding.profile_id: binding
            for binding in config.bindings
            if binding.profile_id is not None
        }
        profile_models: list[tuple[str, Model]] = []
        for profile_id, profile, connection in config.profile_catalog:
            resolved = bound_profiles.get(profile_id)
            fingerprint = (
                resolved.configuration_fingerprint
                if resolved is not None
                else ResolvedProfile(
                    purpose="catalog",
                    profile=profile,
                    connection=connection,
                    selection_source="default",
                    profile_id=profile_id,
                ).configuration_fingerprint
            )
            profile_models.append(
                (
                    profile_id,
                    transport_for(
                        reference=connection.api_key_env,
                        fingerprint=fingerprint,
                        base_url=connection.base_url,
                        profile=profile,
                    ),
                )
            )

        model_by_profile = dict(profile_models)
        models: list[tuple[str, Model]] = []
        for binding in config.bindings:
            reference = binding.connection.api_key_env
            raw_model = model_by_profile.get(binding.profile_id or "")
            if raw_model is None:
                raw_model = transport_for(
                    reference=reference,
                    fingerprint=binding.configuration_fingerprint,
                    base_url=binding.connection.base_url,
                    profile=binding.profile,
                )
            models.append(
                (
                    binding.purpose,
                    with_identity(
                        raw_model,
                        config.identity_for(binding.purpose),
                        retry_runtime=active_retry_runtime,
                    ),
                )
            )
        return cls(
            ModelBindings(
                tuple(models),
                configuration=config if config.profile_catalog else None,
                profile_models=tuple(profile_models),
                retry_runtime=active_retry_runtime,
            ),
            tuple(transports.values()),
        )

    async def aclose(self) -> None:
        for model in self._owned:
            await model.aclose()
