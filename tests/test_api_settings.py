"""Local settings HTTP contract: safe provider status and persistent preferences."""

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

from fastapi.testclient import TestClient

from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.providers.base import Completion, Message, Role, ToolSpec
from grandquiz.providers.speech import TranscriptionRequest, TranscriptionResult


class _ConfiguredProvider:
    secret_token = "llm-secret-must-never-cross-http"
    model_for_role: ClassVar[dict[str, str]] = {
        "basic": "deepseek-v4-pro",
        "enrich": "qwen-plus",
    }
    execution_config_for_role: ClassVar[dict[str, SimpleNamespace]] = {
        "basic": SimpleNamespace(endpoint_host="api.deepseek.com"),
        "enrich": SimpleNamespace(endpoint_host="dashscope.aliyuncs.com"),
    }

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        raise AssertionError("settings must not call the LLM provider")


class _ConfiguredSpeechProvider:
    secret_token = "speech-secret-must-never-cross-http"
    model = "qwen-audio-3.0-asr-flash"
    region = "cn-beijing"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        raise AssertionError("settings must not call the speech provider")


def _app(tmp_path: Path, *, asr_hints_default: bool = False):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=_ConfiguredProvider(),
        speech_provider=_ConfiguredSpeechProvider(),
        asr_hints_default=asr_hints_default,
    )


def test_settings_expose_safe_provider_status_without_secret_values(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, asr_hints_default=True)) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "settings.v1"
    assert payload["preferences"] == {
        "question_language": "中文",
        "difficulty_mode": "adaptive",
        "asr_material_hints_enabled": True,
        "asr_material_hints_source": "environment_default",
    }
    assert payload["difficulty"] == {
        "default_tier": 3,
        "item_count": 0,
        "average_tier": None,
        "tier_counts": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
    }
    assert payload["providers"] == [
        {
            "role": "basic",
            "configured": True,
            "model": "deepseek-v4-pro",
            "endpoint_host": "api.deepseek.com",
            "credential_source": "environment",
            "editable_in_web": False,
            "required_env_vars": ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"],
        },
        {
            "role": "enrich",
            "configured": True,
            "model": "qwen-plus",
            "endpoint_host": "dashscope.aliyuncs.com",
            "credential_source": "environment",
            "editable_in_web": False,
            "required_env_vars": [
                "ENRICH_LLM_API_KEY",
                "ENRICH_LLM_BASE_URL",
                "ENRICH_LLM_MODEL",
            ],
        },
        {
            "role": "speech",
            "configured": True,
            "model": "qwen-audio-3.0-asr-flash",
            "endpoint_host": "cn-beijing",
            "credential_source": "environment",
            "editable_in_web": False,
            "required_env_vars": ["DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"],
        },
    ]
    serialized = response.text
    assert "llm-secret-must-never-cross-http" not in serialized
    assert "speech-secret-must-never-cross-http" not in serialized


def test_settings_patch_is_hot_and_persists_across_restart(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        updated = client.patch(
            "/api/v1/settings",
            json={
                "question_language": "英文",
                "difficulty_mode": "challenge",
                "asr_material_hints_enabled": True,
            },
        )
        voice_config = client.get("/api/v1/voice/config")

    assert updated.status_code == 200
    assert updated.json()["preferences"] == {
        "question_language": "英文",
        "difficulty_mode": "challenge",
        "asr_material_hints_enabled": True,
        "asr_material_hints_source": "preference",
    }
    assert voice_config.json()["hints_enabled"] is True

    with TestClient(_app(tmp_path, asr_hints_default=False)) as restarted:
        persisted = restarted.get("/api/v1/settings")

    assert persisted.json()["preferences"] == updated.json()["preferences"]


def test_settings_rejects_secret_like_unknown_fields(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.patch(
            "/api/v1/settings",
            json={"api_key": "must-not-be-accepted-by-this-contract"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "message": "请求参数无效",
        "retryable": False,
        "trace_id": None,
    }
