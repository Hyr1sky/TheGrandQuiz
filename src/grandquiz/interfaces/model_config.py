"""Application-owned purpose registry and explicit configuration/legacy entry point."""

from collections.abc import Collection, Mapping
from pathlib import Path

from grandquiz.providers.llm import read_legacy_role_configs
from grandquiz.providers.models import ModelRuntime
from grandquiz.providers.profiles import (
    ModelConfiguration,
    ModelConfigurationError,
    ModelConnection,
    ModelProfile,
    ModelRequestRequirements,
    ResolvedProfile,
    parse_model_config,
)

PRODUCT_MODEL_PURPOSES = frozenset(
    {
        "chat",
        "question_generation",
        "answer_grading",
        "distractor_review",
        "material_reading",
        "grounded_answer",
        "summarization",
    }
)
EVAL_MODEL_PURPOSES = frozenset({"eval_quality"})

# Interactive ReAct callers expose tools and native token streaming. An explicit
# choice must prove both capabilities before a turn starts; the legacy/default
# path remains backward compatible until every existing profile declares facts.
CHAT_MODEL_REQUIREMENTS = ModelRequestRequirements(capabilities=("tools", "native_streaming"))


def load_model_configuration(
    *,
    environment: Mapping[str, str],
    purposes: Collection[str] = PRODUCT_MODEL_PURPOSES,
) -> ModelConfiguration:
    """Read one explicit file or import one complete legacy configuration, never merge them."""
    config_path = environment.get("GRANDQUIZ_MODEL_CONFIG", "").strip()
    if config_path:
        try:
            text = Path(config_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ModelConfigurationError("invalid_configuration") from None
        return parse_model_config(text, purposes=purposes)
    try:
        roles = read_legacy_role_configs(environment)
        bindings: list[ResolvedProfile] = []
        for purpose in sorted(purposes):
            if purpose not in PRODUCT_MODEL_PURPOSES | EVAL_MODEL_PURPOSES:
                raise ModelConfigurationError("unknown_purpose")
            role = roles["enrich" if purpose == "question_generation" else "basic"]
            bindings.append(
                ResolvedProfile(
                    purpose=purpose,
                    profile=ModelProfile(
                        connection="legacy",
                        model=role.model,
                        timeout_seconds=role.timeout_seconds,
                        api_dialect=role.api_dialect,
                        thinking_mode=role.thinking_mode,
                        reasoning_effort=role.reasoning_effort,
                        only_provider=role.only_provider,
                    ),
                    connection=ModelConnection(
                        base_url=role.base_url,
                        api_key_env=f"{role.env_prefix}API_KEY",
                    ),
                    selection_source="legacy",
                )
            )
        return ModelConfiguration(tuple(bindings))
    except ModelConfigurationError:
        raise
    except (RuntimeError, ValueError):
        raise ModelConfigurationError("invalid_configuration") from None


def create_model_runtime(
    *,
    environment: Mapping[str, str],
    purposes: Collection[str] = PRODUCT_MODEL_PURPOSES,
) -> ModelRuntime:
    """Validate the complete application configuration before allocating transports."""
    configuration = load_model_configuration(environment=environment, purposes=purposes)
    return ModelRuntime.from_configuration(configuration, environment=environment)
