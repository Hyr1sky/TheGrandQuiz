"""Pure model configuration: purpose selection without transport or ambient state."""

import hashlib
import json
import re
import tomllib
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal, cast

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from grandquiz.providers.fallback import ProviderFallbackPolicy
from grandquiz.providers.retry import ProviderRetryPolicy

ConfigurationErrorCode = Literal["invalid_configuration", "unknown_purpose", "missing_credential"]
WireAPI = Literal["openai_chat_completions", "anthropic_messages"]
ModelCapability = Literal["tools", "native_streaming", "structured_output", "reasoning"]
_MODEL_CAPABILITIES: tuple[ModelCapability, ...] = (
    "tools",
    "native_streaming",
    "structured_output",
    "reasoning",
)
CapabilityState = Literal["supported", "unsupported", "unknown"]
ModelPreset = Literal["fast", "quality"]
SelectionSource = Literal[
    "default",
    "purpose_override",
    "legacy",
    "explicit_profile",
    "preset_fast",
    "preset_quality",
    "fallback",
]
ModelSelectionErrorCode = Literal[
    "unknown_profile",
    "unknown_preset",
    "capability_unsupported",
    "capability_unknown",
    "fallback_disabled",
    "context_window_insufficient",
    "output_limit_insufficient",
    "identity_conflict",
]


class ModelConfigurationError(ValueError):
    """Local configuration error; no user-supplied text escapes in str/repr."""

    def __init__(self, code: ConfigurationErrorCode) -> None:
        self.code = code
        super().__init__(
            {
                "invalid_configuration": "模型配置无效",
                "unknown_purpose": "未注册的模型调用用途",
                "missing_credential": "模型连接缺少凭证",
            }[code]
        )


class ModelSelectionError(ValueError):
    """Safe local selection failure; profile labels never escape through the error."""

    def __init__(
        self,
        code: ModelSelectionErrorCode,
        *,
        capability: ModelCapability | None = None,
    ) -> None:
        self.code = code
        self.capability = capability
        super().__init__(
            {
                "unknown_profile": "所选模型配置不存在",
                "unknown_preset": "所选模型预设未配置",
                "capability_unsupported": "所选模型不支持本次请求能力",
                "capability_unknown": "所选模型能力尚未确认",
                "fallback_disabled": "模型故障切换策略未启用",
                "context_window_insufficient": "候选模型上下文容量不足",
                "output_limit_insufficient": "候选模型输出容量不足",
                "identity_conflict": "候选模型不满足部署身份约束",
            }[code]
        )


class _ConfigRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ModelConnection(_ConfigRecord):
    base_url: str = Field(repr=False)
    api_key_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$", repr=False)
    wire_api: WireAPI = "openai_chat_completions"

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            raise ValueError("invalid endpoint") from None
        if (
            value != value.strip()
            or url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("invalid endpoint")
        return str(url.copy_with(path=url.path.rstrip("/") + "/"))


class ModelCapabilities(_ConfigRecord):
    tools: CapabilityState = "unknown"
    native_streaming: CapabilityState = "unknown"
    structured_output: CapabilityState = "unknown"
    reasoning: CapabilityState = "unknown"

    def state_of(self, capability: ModelCapability) -> CapabilityState:
        return cast("CapabilityState", getattr(self, capability))


class ModelProfile(_ConfigRecord):
    connection: str
    model: str = Field(min_length=1, max_length=256, repr=False)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    api_dialect: Literal["generic", "deepseek", "dashscope"] = "generic"
    thinking_mode: Literal["provider_default", "enabled", "disabled"] = "provider_default"
    reasoning_effort: Literal["high", "max"] | None = None
    only_provider: str | None = None
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("invalid model")
        return value

    @model_validator(mode="after")
    def validate_dialect_parameters(self) -> "ModelProfile":
        if self.reasoning_effort is not None and (
            self.api_dialect != "deepseek" or self.thinking_mode == "disabled"
        ):
            raise ValueError("unsupported dialect parameter combination")
        return self


def _empty_presets() -> dict[ModelPreset, str]:
    return {}


class _Document(_ConfigRecord):
    schema_version: Literal["model-config.v1"]
    default_profile: str
    connections: dict[str, ModelConnection]
    profiles: dict[str, ModelProfile]
    purpose_overrides: dict[str, str] = Field(default_factory=dict)
    presets: dict[ModelPreset, str] = Field(default_factory=_empty_presets)
    retry: ProviderRetryPolicy = Field(default_factory=ProviderRetryPolicy)
    fallback: ProviderFallbackPolicy = Field(default_factory=ProviderFallbackPolicy)
    fallback_candidates: dict[str, list[str]] = Field(default_factory=dict)


class ModelSelection(_ConfigRecord):
    profile_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    )
    preset: ModelPreset | None = None
    fallback_profile_ids: tuple[str, ...] | None = Field(default=None, max_length=8)

    @field_validator("fallback_profile_ids", mode="before")
    @classmethod
    def freeze_fallback_identifiers(cls, value: object) -> object:
        return tuple(cast("list[object]", value)) if isinstance(value, list) else value

    @field_validator("fallback_profile_ids")
    @classmethod
    def fallback_identifiers_are_valid(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) is None for item in value):
            raise ValueError("invalid fallback profile")
        return value

    @model_validator(mode="after")
    def exactly_one_selection(self) -> "ModelSelection":
        if (self.profile_id is None) == (self.preset is None):
            raise ValueError("exactly one model selection is required")
        fallback_profile_ids = self.fallback_profile_ids or ()
        if (
            len(fallback_profile_ids) != len(set(fallback_profile_ids))
            or self.profile_id in fallback_profile_ids
        ):
            raise ValueError("fallback profiles must be unique and exclude the primary")
        return self


class ModelRequestRequirements(_ConfigRecord):
    capabilities: tuple[ModelCapability, ...] = ()
    minimum_context_tokens: int | None = Field(default=None, ge=1)
    minimum_output_tokens: int | None = Field(default=None, ge=1)
    excluded_configuration_fingerprints: tuple[str, ...] = ()

    @field_validator("capabilities")
    @classmethod
    def capabilities_are_unique(
        cls, value: tuple[ModelCapability, ...]
    ) -> tuple[ModelCapability, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate capability")
        return value

    @field_validator("excluded_configuration_fingerprints")
    @classmethod
    def excluded_identities_are_valid(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None for fingerprint in value
        ):
            raise ValueError("invalid excluded model identity")
        return value


class ModelSelectionOption(_ConfigRecord):
    """Safe local control-plane option; excludes model, endpoint, and credentials."""

    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    presets: tuple[ModelPreset, ...] = ()
    capabilities: ModelCapabilities


@dataclass(frozen=True)
class ResolvedProfile:
    purpose: str
    profile: ModelProfile
    connection: ModelConnection
    selection_source: SelectionSource
    profile_id: str | None = None

    @property
    def configuration_fingerprint(self) -> str:
        return _fingerprint(
            {
                "version": "model-request-config.v1",
                "endpoint": self.connection.base_url,
                "wire_api": self.connection.wire_api,
                "temperature": 0,
                "parameters": self.profile.model_dump(exclude={"connection", "capabilities"}),
            }
        )


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ModelIdentity(_ConfigRecord):
    """Safe execution facts; intentionally excludes private labels and connection details."""

    schema_version: Literal["model-identity.v1"] = "model-identity.v1"
    purpose: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    selection_source: SelectionSource
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ModelConfiguration:
    """Immutable validated bindings; resolution never reads environment or network."""

    bindings: tuple[ResolvedProfile, ...]
    profile_catalog: tuple[tuple[str, ModelProfile, ModelConnection], ...] = ()
    presets: tuple[tuple[ModelPreset, str], ...] = ()
    retry_policy: ProviderRetryPolicy = field(default_factory=ProviderRetryPolicy)
    fallback_policy: ProviderFallbackPolicy = field(default_factory=ProviderFallbackPolicy)
    fallback_candidates: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def resolve(self, purpose: str) -> ResolvedProfile:
        for binding in self.bindings:
            if binding.purpose == purpose:
                return binding
        raise ModelConfigurationError("unknown_purpose")

    def identity_for(self, purpose: str) -> ModelIdentity:
        candidates = self.resolve_candidates(purpose)
        return self.identities_for_candidates(candidates)[0]

    def identity_for_resolved(self, binding: ResolvedProfile) -> ModelIdentity:
        return ModelIdentity(
            purpose=binding.purpose,
            selection_source=binding.selection_source,
            configuration_fingerprint=binding.configuration_fingerprint,
            policy_fingerprint=_fingerprint(
                {
                    "version": "purpose-selection.v1",
                    "purpose": binding.purpose,
                    "selection_source": binding.selection_source,
                    "capabilities": binding.profile.capabilities.model_dump(),
                }
            ),
        )

    def resolve_candidates(
        self,
        purpose: str,
        *,
        requirements: ModelRequestRequirements | None = None,
    ) -> tuple[ResolvedProfile, ...]:
        primary = self.resolve(purpose)
        alternatives = next(
            (profile_ids for name, profile_ids in self.fallback_candidates if name == purpose),
            (),
        )
        return self._resolve_candidate_chain(
            primary,
            alternatives,
            requirements=requirements,
        )

    def resolve_selected_candidates(
        self,
        purpose: str,
        selection: ModelSelection,
        *,
        requirements: ModelRequestRequirements | None = None,
    ) -> tuple[ResolvedProfile, ...]:
        primary = self.resolve_selection(purpose, selection, requirements=requirements)
        alternatives = selection.fallback_profile_ids or ()
        if alternatives and not self.fallback_policy.enabled:
            raise ModelSelectionError("fallback_disabled")
        return self._resolve_candidate_chain(
            primary,
            alternatives,
            requirements=requirements,
        )

    def identities_for_candidates(
        self,
        candidates: tuple[ResolvedProfile, ...],
    ) -> tuple[ModelIdentity, ...]:
        if len(candidates) == 1:
            return (self.identity_for_resolved(candidates[0]),)
        policy_fingerprint = _fingerprint(
            {
                "version": "fallback-selection.v1",
                "purpose": candidates[0].purpose,
                "candidate_configuration_fingerprints": [
                    candidate.configuration_fingerprint for candidate in candidates
                ],
                "fallback_policy": self.fallback_policy.fingerprint,
                "retry_policy": self.retry_policy.fingerprint,
            }
        )
        return tuple(
            ModelIdentity(
                purpose=candidate.purpose,
                selection_source=candidate.selection_source,
                configuration_fingerprint=candidate.configuration_fingerprint,
                policy_fingerprint=policy_fingerprint,
            )
            for candidate in candidates
        )

    def _resolve_candidate_chain(
        self,
        primary: ResolvedProfile,
        alternatives: tuple[str, ...],
        *,
        requirements: ModelRequestRequirements | None,
    ) -> tuple[ResolvedProfile, ...]:
        if alternatives and not self.fallback_policy.enabled:
            raise ModelSelectionError("fallback_disabled")
        if primary.profile_id in alternatives or len(alternatives) != len(set(alternatives)):
            raise ModelSelectionError("identity_conflict")
        candidates = [primary]
        for profile_id in alternatives:
            resolved = self._catalog_profile(
                primary.purpose,
                profile_id,
                selection_source="fallback",
            )
            candidates.append(resolved)
        fingerprints = [candidate.configuration_fingerprint for candidate in candidates]
        if len(fingerprints) != len(set(fingerprints)):
            raise ModelSelectionError("identity_conflict")
        if len(candidates) > 1:
            primary_profile = candidates[0].profile
            for candidate in candidates[1:]:
                for capability in _MODEL_CAPABILITIES:
                    if (
                        primary_profile.capabilities.state_of(capability) == "supported"
                        and candidate.profile.capabilities.state_of(capability) != "supported"
                    ):
                        raise ModelSelectionError(
                            "capability_unsupported",
                            capability=capability,
                        )
                if (
                    primary_profile.context_window_tokens is None
                    or candidate.profile.context_window_tokens is None
                    or candidate.profile.context_window_tokens
                    < primary_profile.context_window_tokens
                ):
                    raise ModelSelectionError("context_window_insufficient")
                if (
                    primary_profile.max_output_tokens is None
                    or candidate.profile.max_output_tokens is None
                    or candidate.profile.max_output_tokens < primary_profile.max_output_tokens
                ):
                    raise ModelSelectionError("output_limit_insufficient")
        for candidate in candidates:
            self._ensure_eligible(candidate, requirements)
        return tuple(candidates)

    def _catalog_profile(
        self,
        purpose: str,
        profile_id: str,
        *,
        selection_source: SelectionSource,
    ) -> ResolvedProfile:
        resolved = next(
            (
                ResolvedProfile(
                    purpose=purpose,
                    profile=profile,
                    connection=connection,
                    selection_source=selection_source,
                    profile_id=name,
                )
                for name, profile, connection in self.profile_catalog
                if name == profile_id
            ),
            None,
        )
        if resolved is None:
            raise ModelSelectionError("unknown_profile")
        return resolved

    @staticmethod
    def _ensure_eligible(
        resolved: ResolvedProfile,
        requirements: ModelRequestRequirements | None,
    ) -> None:
        active = requirements or ModelRequestRequirements()
        for capability in active.capabilities:
            state = resolved.profile.capabilities.state_of(capability)
            if state == "unsupported":
                raise ModelSelectionError("capability_unsupported", capability=capability)
            if state == "unknown":
                raise ModelSelectionError("capability_unknown", capability=capability)
        if active.minimum_context_tokens is not None and (
            resolved.profile.context_window_tokens is None
            or resolved.profile.context_window_tokens < active.minimum_context_tokens
        ):
            raise ModelSelectionError("context_window_insufficient")
        if active.minimum_output_tokens is not None and (
            resolved.profile.max_output_tokens is None
            or resolved.profile.max_output_tokens < active.minimum_output_tokens
        ):
            raise ModelSelectionError("output_limit_insufficient")
        if resolved.configuration_fingerprint in active.excluded_configuration_fingerprints:
            raise ModelSelectionError("identity_conflict")

    def resolve_selection(
        self,
        purpose: str,
        selection: ModelSelection,
        *,
        requirements: ModelRequestRequirements | None = None,
    ) -> ResolvedProfile:
        self.resolve(purpose)
        profile_id = selection.profile_id
        selection_source: SelectionSource = "explicit_profile"
        if selection.preset is not None:
            profile_id = next(
                (value for name, value in self.presets if name == selection.preset),
                None,
            )
            if profile_id is None:
                raise ModelSelectionError("unknown_preset")
            selection_source = "preset_fast" if selection.preset == "fast" else "preset_quality"
        resolved = next(
            (
                ResolvedProfile(
                    purpose=purpose,
                    profile=profile,
                    connection=connection,
                    selection_source=selection_source,
                    profile_id=name,
                )
                for name, profile, connection in self.profile_catalog
                if name == profile_id
            ),
            None,
        )
        if resolved is None:
            raise ModelSelectionError("unknown_profile")
        self._ensure_eligible(resolved, requirements)
        return resolved


def parse_model_config(text: str, *, purposes: Collection[str]) -> ModelConfiguration:
    try:
        document = _Document.model_validate(tomllib.loads(text))
        identifiers = [*document.connections, *document.profiles, *purposes]
        if not purposes or any(
            re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) is None for key in identifiers
        ):
            raise ValueError("invalid identifier")
        if (
            document.default_profile not in document.profiles
            or not set(document.purpose_overrides) <= set(purposes)
            or any(value not in document.profiles for value in document.purpose_overrides.values())
            or any(value not in document.profiles for value in document.presets.values())
            or any(
                profile.connection not in document.connections
                for profile in document.profiles.values()
            )
            or (bool(document.fallback_candidates) and not document.fallback.enabled)
            or not set(document.fallback_candidates) <= set(purposes)
            or any(
                not candidates
                or len(candidates) > 8
                or len(candidates) != len(set(candidates))
                or any(candidate not in document.profiles for candidate in candidates)
                or document.purpose_overrides.get(purpose, document.default_profile) in candidates
                for purpose, candidates in document.fallback_candidates.items()
            )
        ):
            raise ValueError("invalid reference")
        for profile in document.profiles.values():
            connection = document.connections[profile.connection]
            if connection.wire_api == "anthropic_messages" and (
                profile.max_output_tokens is None
                or profile.api_dialect != "generic"
                or profile.thinking_mode == "enabled"
                or profile.reasoning_effort is not None
                or profile.only_provider is not None
                or profile.capabilities.reasoning == "supported"
                or profile.capabilities.structured_output == "supported"
            ):
                raise ValueError("unsupported Anthropic profile options")
        for purpose, alternatives in document.fallback_candidates.items():
            profile_ids = [
                document.purpose_overrides.get(purpose, document.default_profile),
                *alternatives,
            ]
            fingerprints = [
                ResolvedProfile(
                    purpose=purpose,
                    profile=(profile := document.profiles[profile_id]),
                    connection=document.connections[profile.connection],
                    selection_source="default",
                    profile_id=profile_id,
                ).configuration_fingerprint
                for profile_id in profile_ids
            ]
            if len(fingerprints) != len(set(fingerprints)):
                raise ValueError("duplicate fallback deployment")
            primary_profile = document.profiles[profile_ids[0]]
            for profile_id in profile_ids[1:]:
                candidate_profile = document.profiles[profile_id]
                if any(
                    primary_profile.capabilities.state_of(capability) == "supported"
                    and candidate_profile.capabilities.state_of(capability) != "supported"
                    for capability in _MODEL_CAPABILITIES
                ):
                    raise ValueError("fallback capability is insufficient")
                if (
                    primary_profile.context_window_tokens is None
                    or candidate_profile.context_window_tokens is None
                    or candidate_profile.context_window_tokens
                    < primary_profile.context_window_tokens
                    or primary_profile.max_output_tokens is None
                    or candidate_profile.max_output_tokens is None
                    or candidate_profile.max_output_tokens < primary_profile.max_output_tokens
                ):
                    raise ValueError("fallback capacity is insufficient")
    except (ValueError, ValidationError):
        raise ModelConfigurationError("invalid_configuration") from None
    return ModelConfiguration(
        bindings=tuple(
            ResolvedProfile(
                purpose=purpose,
                profile=(
                    profile := document.profiles[
                        document.purpose_overrides.get(purpose, document.default_profile)
                    ]
                ),
                connection=document.connections[profile.connection],
                selection_source="purpose_override"
                if purpose in document.purpose_overrides
                else "default",
                profile_id=document.purpose_overrides.get(purpose, document.default_profile),
            )
            for purpose in sorted(purposes)
        ),
        profile_catalog=tuple(
            (
                profile_id,
                profile,
                document.connections[profile.connection],
            )
            for profile_id, profile in sorted(document.profiles.items())
        ),
        presets=tuple(sorted(document.presets.items())),
        retry_policy=document.retry,
        fallback_policy=document.fallback,
        fallback_candidates=tuple(
            (purpose, tuple(candidates))
            for purpose, candidates in sorted(document.fallback_candidates.items())
        ),
    )
