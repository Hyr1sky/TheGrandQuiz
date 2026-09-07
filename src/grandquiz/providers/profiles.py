"""Pure model configuration: purpose selection without transport or ambient state."""

import hashlib
import json
import re
import tomllib
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

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


class ModelProfile(_ConfigRecord):
    connection: str
    model: str = Field(min_length=1, max_length=256, repr=False)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    api_dialect: Literal["generic", "deepseek", "dashscope"] = "generic"
    thinking_mode: Literal["provider_default", "enabled", "disabled"] = "provider_default"
    reasoning_effort: Literal["high", "max"] | None = None
    only_provider: str | None = None

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


class _Document(_ConfigRecord):
    schema_version: Literal["model-config.v1"]
    default_profile: str
    connections: dict[str, ModelConnection]
    profiles: dict[str, ModelProfile]
    purpose_overrides: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedProfile:
    purpose: str
    profile: ModelProfile
    connection: ModelConnection
    selection_source: Literal["default", "purpose_override", "legacy"]

    @property
    def configuration_fingerprint(self) -> str:
        return _fingerprint(
            {
                "version": "model-request-config.v1",
                "endpoint": self.connection.base_url,
                "wire_api": self.connection.wire_api,
                "temperature": 0,
                "parameters": self.profile.model_dump(exclude={"connection"}),
            }
        )


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ModelIdentity(_ConfigRecord):
    """Safe execution facts; intentionally excludes private labels and connection details."""

    schema_version: Literal["model-identity.v1"] = "model-identity.v1"
    purpose: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    selection_source: Literal["default", "purpose_override", "legacy"]
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ModelConfiguration:
    """Immutable validated bindings; resolution never reads environment or network."""

    bindings: tuple[ResolvedProfile, ...]

    def resolve(self, purpose: str) -> ResolvedProfile:
        for binding in self.bindings:
            if binding.purpose == purpose:
                return binding
        raise ModelConfigurationError("unknown_purpose")

    def identity_for(self, purpose: str) -> ModelIdentity:
        binding = self.resolve(purpose)
        return ModelIdentity(
            purpose=purpose,
            selection_source=binding.selection_source,
            configuration_fingerprint=binding.configuration_fingerprint,
            policy_fingerprint=_fingerprint(
                {
                    "version": "purpose-selection.v1",
                    "purpose": binding.purpose,
                    "selection_source": binding.selection_source,
                }
            ),
        )


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
            )
            for purpose in sorted(purposes)
        )
    )
