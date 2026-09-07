"""Pure public configuration contract: no SDK, filesystem, or ambient credentials."""

import pytest

from grandquiz.providers.profiles import (
    ModelCapability,
    ModelConfigurationError,
    ModelRequestRequirements,
    ModelSelection,
    ModelSelectionError,
    parse_model_config,
)

CONFIG = """
schema_version = "model-config.v1"
default_profile = "shared"
[connections.primary]
base_url = "https://models.example.test/tenant-a/v1"
api_key_env = "TEST_MODEL_KEY"
wire_api = "openai_chat_completions"
[profiles.shared]
connection = "primary"
model = "test-model"
[profiles.writer]
connection = "primary"
model = "test-writer"
[purpose_overrides]
question_generation = "writer"
"""

SELECTABLE_CONFIG = (
    CONFIG
    + """
[presets]
fast = "shared"
quality = "writer"
[profiles.shared.capabilities]
tools = "supported"
native_streaming = "unsupported"
structured_output = "unknown"
reasoning = "unknown"
[profiles.writer.capabilities]
tools = "supported"
native_streaming = "supported"
structured_output = "unsupported"
reasoning = "supported"
"""
)


def test_explicit_profile_and_preset_resolve_from_the_frozen_catalog() -> None:
    config = parse_model_config(
        SELECTABLE_CONFIG,
        purposes={"chat", "answer_grading", "question_generation"},
    )
    requirements = ModelRequestRequirements(capabilities=("tools", "native_streaming"))

    explicit = config.resolve_selection(
        "chat",
        ModelSelection(profile_id="writer"),
        requirements=requirements,
    )
    quality = config.resolve_selection(
        "chat",
        ModelSelection(preset="quality"),
        requirements=requirements,
    )

    assert explicit.profile.model == quality.profile.model == "test-writer"
    assert explicit.selection_source == "explicit_profile"
    assert quality.selection_source == "preset_quality"
    assert config.identity_for_resolved(explicit).purpose == "chat"
    assert config.identity_for_resolved(explicit).selection_source == "explicit_profile"


def test_concrete_profile_and_preset_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError):
        ModelSelection(profile_id="writer", preset="quality")
    with pytest.raises(ValueError):
        ModelSelection()


@pytest.mark.parametrize(
    "selection,expected_code,expected_capability",
    [
        (
            ModelSelection(preset="fast"),
            "capability_unsupported",
            "native_streaming",
        ),
        (
            ModelSelection(profile_id="shared"),
            "capability_unknown",
            "structured_output",
        ),
    ],
)
def test_explicit_selection_distinguishes_unsupported_from_unknown_capability(
    selection: ModelSelection,
    expected_code: str,
    expected_capability: ModelCapability,
) -> None:
    config = parse_model_config(
        SELECTABLE_CONFIG,
        purposes={"chat", "question_generation"},
    )
    requirements = ModelRequestRequirements(capabilities=(expected_capability,))

    with pytest.raises(ModelSelectionError) as caught:
        config.resolve_selection("chat", selection, requirements=requirements)

    assert caught.value.code == expected_code
    assert caught.value.capability == expected_capability
    assert "test-model" not in str(caught.value)


def test_default_binding_keeps_legacy_unknown_capabilities_compatible() -> None:
    config = parse_model_config(CONFIG, purposes={"chat", "question_generation"})
    assert config.resolve("chat").profile.model == "test-model"
    assert config.retry_policy.max_attempts == 3


def test_retry_policy_is_validated_as_part_of_model_configuration() -> None:
    config = parse_model_config(
        CONFIG
        + """
[retry]
max_attempts = 4
deadline_seconds = 45.0
base_delay_seconds = 1.0
max_delay_seconds = 6.0
max_total_wait_seconds = 15.0
jitter_ratio = 0.1
""",
        purposes={"chat", "question_generation"},
    )

    assert config.retry_policy.max_attempts == 4
    assert config.retry_policy.deadline_seconds == 45.0
    assert config.retry_policy.fingerprint != ""

    with pytest.raises(ModelConfigurationError):
        parse_model_config(
            CONFIG + "\n[retry]\nmax_attempts = 0\n",
            purposes={"chat", "question_generation"},
        )


FALLBACK_CONFIG = (
    SELECTABLE_CONFIG.replace(
        "[profiles.shared]",
        "[profiles.shared]\ncontext_window_tokens = 64000\nmax_output_tokens = 4096",
    ).replace(
        "[profiles.writer]",
        "[profiles.writer]\ncontext_window_tokens = 128000\nmax_output_tokens = 8192",
    )
    + """
[fallback]
enabled = true
max_attempts_per_candidate = 2
[fallback_candidates]
chat = ["writer"]
"""
)


def test_configuration_freezes_an_ordered_authorized_fallback_chain() -> None:
    config = parse_model_config(
        FALLBACK_CONFIG,
        purposes={"chat", "question_generation"},
    )

    candidates = config.resolve_candidates("chat")

    assert [candidate.profile_id for candidate in candidates] == ["shared", "writer"]
    assert candidates[0].selection_source == "default"
    assert candidates[1].selection_source == "fallback"
    assert config.fallback_policy.enabled is True
    assert config.fallback_policy.max_attempts_per_candidate == 2
    identities = config.identities_for_candidates(candidates)
    assert identities[0] == config.identity_for("chat")
    assert len({identity.configuration_fingerprint for identity in identities}) == 2
    assert len({identity.policy_fingerprint for identity in identities}) == 1


def test_explicit_pin_only_allows_fallback_when_alternatives_are_also_explicit() -> None:
    config = parse_model_config(
        FALLBACK_CONFIG,
        purposes={"chat", "question_generation"},
    )

    pinned = config.resolve_selected_candidates(
        "chat",
        ModelSelection(profile_id="shared"),
    )
    opted_in = config.resolve_selected_candidates(
        "chat",
        ModelSelection(profile_id="shared", fallback_profile_ids=("writer",)),
    )

    assert [candidate.profile_id for candidate in pinned] == ["shared"]
    assert [candidate.profile_id for candidate in opted_in] == ["shared", "writer"]


def test_fallback_candidates_must_be_enabled_known_unique_and_acyclic() -> None:
    cases = [
        FALLBACK_CONFIG.replace("enabled = true", "enabled = false"),
        FALLBACK_CONFIG.replace('["writer"]', '["missing"]'),
        FALLBACK_CONFIG.replace('["writer"]', '["writer", "writer"]'),
        FALLBACK_CONFIG.replace('["writer"]', '["shared"]'),
    ]

    for text in cases:
        with pytest.raises(ModelConfigurationError):
            parse_model_config(text, purposes={"chat", "question_generation"})

    with pytest.raises(ModelConfigurationError):
        parse_model_config(
            FALLBACK_CONFIG.replace(
                '[profiles.writer.capabilities]\ntools = "supported"',
                '[profiles.writer.capabilities]\ntools = "unsupported"',
            ),
            purposes={"chat", "question_generation"},
        )


def test_fallback_eligibility_checks_capability_budget_and_identity_constraints() -> None:
    config = parse_model_config(
        FALLBACK_CONFIG,
        purposes={"chat", "question_generation"},
    )
    writer = config.resolve_selected_candidates(
        "chat",
        ModelSelection(profile_id="writer"),
    )[0]

    eligible = config.resolve_selected_candidates(
        "chat",
        ModelSelection(profile_id="shared", fallback_profile_ids=("writer",)),
        requirements=ModelRequestRequirements(
            capabilities=("tools",),
            minimum_context_tokens=32000,
            minimum_output_tokens=2048,
        ),
    )
    assert len(eligible) == 2

    with pytest.raises(ModelSelectionError) as excluded:
        config.resolve_selected_candidates(
            "chat",
            ModelSelection(profile_id="shared", fallback_profile_ids=("writer",)),
            requirements=ModelRequestRequirements(
                capabilities=("tools",),
                minimum_context_tokens=32000,
                minimum_output_tokens=2048,
                excluded_configuration_fingerprints=(writer.configuration_fingerprint,),
            ),
        )
    assert excluded.value.code == "identity_conflict"

    with pytest.raises(ModelSelectionError) as too_large:
        config.resolve_selected_candidates(
            "chat",
            ModelSelection(profile_id="shared", fallback_profile_ids=("writer",)),
            requirements=ModelRequestRequirements(minimum_context_tokens=256000),
        )
    assert too_large.value.code == "context_window_insufficient"


@pytest.mark.parametrize(
    "selection,expected_code",
    [
        (ModelSelection(profile_id="missing"), "unknown_profile"),
        (ModelSelection(preset="quality"), "unknown_preset"),
    ],
)
def test_unknown_explicit_selection_fails_without_falling_back_to_default(
    selection: ModelSelection,
    expected_code: str,
) -> None:
    config = parse_model_config(CONFIG, purposes={"chat", "question_generation"})
    with pytest.raises(ModelSelectionError) as caught:
        config.resolve_selection("chat", selection)
    assert caught.value.code == expected_code
    assert "missing" not in str(caught.value)


def test_configuration_selects_default_and_only_the_explicit_purpose_override() -> None:
    config = parse_model_config(CONFIG, purposes={"question_generation", "answer_grading"})
    writer = config.resolve("question_generation")
    grader = config.resolve("answer_grading")
    assert writer.profile.model == "test-writer"
    assert writer.selection_source == "purpose_override"
    assert grader.profile.model == "test-model"
    assert grader.selection_source == "default"
    assert writer.connection.api_key_env == grader.connection.api_key_env == "TEST_MODEL_KEY"
    assert grader.profile.timeout_seconds == 60
    assert grader.profile.thinking_mode == "provider_default"


def test_purpose_override_does_not_change_another_purposes_execution_identity() -> None:
    purposes = {"question_generation", "answer_grading"}
    overridden = parse_model_config(CONFIG, purposes=purposes)
    default_only = parse_model_config(
        CONFIG.replace('question_generation = "writer"', "# no purpose override"),
        purposes=purposes,
    )

    assert overridden.identity_for("answer_grading") == default_only.identity_for("answer_grading")
    assert overridden.identity_for("question_generation") != default_only.identity_for(
        "question_generation"
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("model-config.v1", "model-config.v999"),
        ('default_profile = "shared"', 'default_profile = "unknown-secret"'),
        ('connection = "primary"', 'connection = "missing-secret"'),
        ('question_generation = "writer"', 'unknown_secret = "writer"'),
        ("[profiles.shared]", "[profiles.shared]\ntimeout_seconds = -1"),
        ("[profiles.shared]", "[profiles.shared]\ntimeout_seconds = nan"),
        ("[profiles.shared]", '[profiles.shared]\napi_key = "test-secret"'),
        ("[profiles.writer]", '[profiles.writer]\nmodel = "duplicate-secret"'),
        ("test-model", "   "),
        ("TEST_MODEL_KEY", "bad secret reference"),
        (
            "https://models.example.test/tenant-a/v1",
            "https://user:test-secret@models.example.test/v1",
        ),
        (
            "https://models.example.test/tenant-a/v1",
            "https://models.example.test/v1?key=test-secret",
        ),
        ("https://models.example.test/tenant-a/v1", "ftp://models.example.test/v1"),
    ],
)
def test_invalid_configuration_fails_with_a_safe_finite_error(old: str, new: str) -> None:
    with pytest.raises(ModelConfigurationError) as caught:
        parse_model_config(
            CONFIG.replace(old, new), purposes={"question_generation", "answer_grading"}
        )
    assert caught.value.code == "invalid_configuration"
    assert str(caught.value) == "模型配置无效"
    assert "secret" not in repr(caught.value)


def test_unknown_purpose_is_not_silently_assigned_the_default() -> None:
    config = parse_model_config(CONFIG, purposes={"question_generation", "answer_grading"})
    with pytest.raises(ModelConfigurationError) as caught:
        config.resolve("unknown-secret")
    assert caught.value.code == "unknown_purpose"
    assert "unknown-secret" not in str(caught.value)


def test_identity_distinguishes_deployments_and_parameters_but_not_credential_references() -> None:
    purposes = {"question_generation", "answer_grading"}
    identity = parse_model_config(CONFIG, purposes=purposes).identity_for("answer_grading")
    rotated = parse_model_config(CONFIG.replace("TEST_MODEL_KEY", "ROTATED_KEY"), purposes=purposes)
    other_path = parse_model_config(CONFIG.replace("tenant-a", "tenant-b"), purposes=purposes)
    thinking = parse_model_config(
        CONFIG.replace("[profiles.shared]", '[profiles.shared]\nthinking_mode = "enabled"'),
        purposes=purposes,
    )
    assert rotated.identity_for("answer_grading") == identity
    assert (
        other_path.identity_for("answer_grading").configuration_fingerprint
        != identity.configuration_fingerprint
    )
    assert (
        thinking.identity_for("answer_grading").configuration_fingerprint
        != identity.configuration_fingerprint
    )
    assert len(identity.configuration_fingerprint) == 64
    for private_value in ("TEST_MODEL_KEY", "models.example.test", "test-model", "shared"):
        assert private_value not in identity.model_dump_json()
    assert identity.purpose == "answer_grading"
    assert identity.selection_source == "default"


def test_configuration_identity_is_canonical_and_immutable() -> None:
    config = parse_model_config(CONFIG, purposes={"answer_grading", "question_generation"})
    reordered = CONFIG.replace(
        'connection = "primary"\nmodel = "test-model"',
        'model = "test-model"\nconnection = "primary"',
    )
    assert config.identity_for("answer_grading") == parse_model_config(
        reordered, purposes={"question_generation", "answer_grading"}
    ).identity_for("answer_grading")
    with pytest.raises(ValueError):
        config.resolve("answer_grading").profile.model = "changed"


@pytest.mark.parametrize(
    "profile_fields",
    [
        'api_dialect = "dashscope"\nreasoning_effort = "high"',
        ('api_dialect = "deepseek"\nthinking_mode = "disabled"\nreasoning_effort = "high"'),
        'api_dialect = "generic"\nreasoning_effort = "max"',
    ],
)
def test_unsupported_dialect_parameter_combinations_fail_during_parsing(
    profile_fields: str,
) -> None:
    text = CONFIG.replace(
        "[profiles.shared]",
        f"[profiles.shared]\n{profile_fields}",
    )

    with pytest.raises(ModelConfigurationError) as caught:
        parse_model_config(text, purposes={"question_generation", "answer_grading"})

    assert caught.value.code == "invalid_configuration"
