"""Explicit file loading and bounded legacy import, without environment mutation."""

from pathlib import Path

import pytest

from grandquiz.interfaces.model_config import load_model_configuration
from grandquiz.providers.profiles import ModelConfigurationError


def legacy_environment() -> dict[str, str]:
    return {
        "LLM_API_KEY": "test-default-key",
        "LLM_BASE_URL": "https://api.deepseek.com/v1",
        "LLM_MODEL": "default-model",
    }


def test_legacy_import_preserves_the_original_question_model_distribution() -> None:
    environment = legacy_environment() | {
        "ENRICH_LLM_API_KEY": "test-writer-key",
        "ENRICH_LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "ENRICH_LLM_MODEL": "writer-model",
    }
    config = load_model_configuration(environment=environment)
    assert config.resolve("question_generation").profile.model == "writer-model"
    for purpose in (
        "answer_grading",
        "distractor_review",
        "material_reading",
        "chat",
        "summarization",
        "grounded_answer",
    ):
        assert config.resolve(purpose).profile.model == "default-model"
    assert config.resolve("question_generation").profile.api_dialect == "dashscope"
    assert config.resolve("answer_grading").profile.api_dialect == "deepseek"
    assert config.identity_for("question_generation").selection_source == "legacy"
    assert "test-default-key" not in repr(config)


def test_single_legacy_config_is_imported_without_requiring_enrich() -> None:
    config = load_model_configuration(environment=legacy_environment())
    assert config.resolve("question_generation").connection.api_key_env == "LLM_API_KEY"
    assert config.resolve("answer_grading").profile == config.resolve("question_generation").profile


def test_explicit_file_never_borrows_legacy_fields_or_silently_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text('schema_version = "model-config.v1"\ndefault_profile = "missing"')
    environment = legacy_environment() | {"GRANDQUIZ_MODEL_CONFIG": str(path)}
    with pytest.raises(ModelConfigurationError):
        load_model_configuration(environment=environment)
    path.unlink()
    with pytest.raises(ModelConfigurationError):
        load_model_configuration(environment=environment)
