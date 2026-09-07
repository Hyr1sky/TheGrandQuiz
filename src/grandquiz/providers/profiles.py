"""Pure model configuration: purpose selection without transport or ambient state."""

import hashlib
import json
import re
import tomllib
from collections.abc import Collection
from dataclasses import dataclass
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

ConfigurationErrorCode = Literal["invalid_configuration", "unknown_purpose", "missing_credential"]
ModelCapability = Literal["tools", "native_streaming", "structured_output", "reasoning"]
CapabilityState = Literal["supported", "unsupported", "unknown"]
ModelPreset = Literal["fast", "quality"]
SelectionSource = Literal[
    "default",
    "purpose_override",
    "legacy",
    "explicit_profile",
    "preset_fast",
    "preset_quality",
]
ModelSelectionErrorCode = Literal[
    "unknown_profile",
    "unknown_preset",
    "capability_unsupported",
    "capability_unknown",
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
            }[code]
        )


class _ConfigRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ModelConnection(_ConfigRecord):
    base_url: str = Field(repr=False)
    api_key_env: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,127}$", repr=False)
    wire_api: Literal["openai_chat_completions"] = "openai_chat_completions"

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


class ModelSelection(_ConfigRecord):
    profile_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    )
    preset: ModelPreset | None = None

    @model_validator(mode="after")
    def exactly_one_selection(self) -> "ModelSelection":
        if (self.profile_id is None) == (self.preset is None):
            raise ValueError("exactly one model selection is required")
        return self


class ModelRequestRequirements(_ConfigRecord):
    capabilities: tuple[ModelCapability, ...] = ()

    @field_validator("capabilities")
    @classmethod
    def capabilities_are_unique(
        cls, value: tuple[ModelCapability, ...]
    ) -> tuple[ModelCapability, ...]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate capability")
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

    def resolve(self, purpose: str) -> ResolvedProfile:
        for binding in self.bindings:
            if binding.purpose == purpose:
                return binding
        raise ModelConfigurationError("unknown_purpose")

    def identity_for(self, purpose: str) -> ModelIdentity:
        return self.identity_for_resolved(self.resolve(purpose))

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
        for capability in (requirements or ModelRequestRequirements()).capabilities:
            state = resolved.profile.capabilities.state_of(capability)
            if state == "unsupported":
                raise ModelSelectionError(
                    "capability_unsupported",
                    capability=capability,
                )
            if state == "unknown":
                raise ModelSelectionError(
                    "capability_unknown",
                    capability=capability,
                )
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
        ):
            raise ValueError("invalid reference")
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
    )
