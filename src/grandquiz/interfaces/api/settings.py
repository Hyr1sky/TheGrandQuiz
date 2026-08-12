"""Safe local settings projection and explicit preference commands."""

from __future__ import annotations

from collections import Counter
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.domain.learning.preference import (
    ASR_MATERIAL_HINTS_KEY,
    DIFFICULTY_MODE_KEY,
    QUESTION_LANGUAGE_KEY,
    DifficultyMode,
    QuestionLanguage,
    resolve_asr_material_hints,
    resolve_difficulty_mode,
    resolve_question_language,
)
from grandquiz.providers.base import Provider
from grandquiz.providers.speech import SpeechRecognitionProvider


class VoiceHintPolicy(Protocol):
    def set_hints_enabled(self, enabled: bool) -> None: ...


class ProviderSettingView(BaseModel):
    role: Literal["basic", "enrich", "speech"]
    configured: bool
    model: str | None
    endpoint_host: str | None
    credential_source: Literal["environment"] = "environment"
    editable_in_web: Literal[False] = False
    required_env_vars: list[str]


class PreferenceSettingView(BaseModel):
    question_language: QuestionLanguage
    difficulty_mode: DifficultyMode
    asr_material_hints_enabled: bool
    asr_material_hints_source: Literal["preference", "environment_default"]


class DifficultySettingView(BaseModel):
    default_tier: Literal[3] = 3
    item_count: int
    average_tier: float | None
    tier_counts: dict[str, int]


class SettingsView(BaseModel):
    schema_version: Literal["settings.v1"] = "settings.v1"
    preferences: PreferenceSettingView
    difficulty: DifficultySettingView
    providers: list[ProviderSettingView]


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_language: QuestionLanguage | None = None
    difficulty_mode: DifficultyMode | None = None
    asr_material_hints_enabled: bool | None = None


class LocalSettings:
    """Own safe projection and validated writes behind one small interface."""

    def __init__(
        self,
        *,
        persistence: LearningPersistence,
        provider: Provider,
        speech_provider: SpeechRecognitionProvider | None,
        voice_hint_policy: VoiceHintPolicy | None,
        asr_hints_default: bool,
    ) -> None:
        self._persistence = persistence
        self._provider = provider
        self._speech_provider = speech_provider
        self._voice_hint_policy = voice_hint_policy
        self._asr_hints_default = asr_hints_default

    def view(self) -> SettingsView:
        preferences = self._persistence.preferences
        asr_preference = preferences.get_preference(ASR_MATERIAL_HINTS_KEY)
        return SettingsView(
            preferences=PreferenceSettingView(
                question_language=resolve_question_language(preferences),
                difficulty_mode=resolve_difficulty_mode(preferences),
                asr_material_hints_enabled=resolve_asr_material_hints(
                    preferences,
                    default=self._asr_hints_default,
                ),
                asr_material_hints_source=(
                    "preference" if asr_preference is not None else "environment_default"
                ),
            ),
            difficulty=self._difficulty_view(),
            providers=self._provider_views(),
        )

    def update(self, patch: SettingsPatch) -> SettingsView:
        preferences = self._persistence.preferences
        with self._persistence.transaction_owner.transaction():
            if patch.question_language is not None:
                preferences.set_preference(QUESTION_LANGUAGE_KEY, patch.question_language)
            if patch.difficulty_mode is not None:
                preferences.set_preference(DIFFICULTY_MODE_KEY, patch.difficulty_mode)
            if patch.asr_material_hints_enabled is not None:
                preferences.set_preference(
                    ASR_MATERIAL_HINTS_KEY,
                    "true" if patch.asr_material_hints_enabled else "false",
                )
        if patch.asr_material_hints_enabled is not None and self._voice_hint_policy is not None:
            self._voice_hint_policy.set_hints_enabled(patch.asr_material_hints_enabled)
        return self.view()

    def _difficulty_view(self) -> DifficultySettingView:
        tiers = [
            self._persistence.difficulty.tier_of(item.item_id)
            for item in self._persistence.store.all_items()
        ]
        counts = Counter(tiers)
        return DifficultySettingView(
            item_count=len(tiers),
            average_tier=None if not tiers else round(sum(tiers) / len(tiers), 2),
            tier_counts={str(tier): counts[tier] for tier in range(1, 6)},
        )

    def _provider_views(self) -> list[ProviderSettingView]:
        models_object = getattr(self._provider, "model_for_role", {})
        models = cast("dict[str, str]", models_object) if isinstance(models_object, dict) else {}
        execution_object = getattr(self._provider, "execution_config_for_role", {})
        execution = (
            cast("dict[str, object]", execution_object)
            if isinstance(execution_object, dict)
            else {}
        )

        def llm(role: Literal["basic", "enrich"]) -> ProviderSettingView:
            config = execution.get(role)
            endpoint = getattr(config, "endpoint_host", None)
            prefix = "LLM_" if role == "basic" else "ENRICH_LLM_"
            return ProviderSettingView(
                role=role,
                configured=True,
                model=models.get(role),
                endpoint_host=endpoint if isinstance(endpoint, str) else None,
                required_env_vars=[
                    f"{prefix}API_KEY",
                    f"{prefix}BASE_URL",
                    f"{prefix}MODEL",
                ],
            )

        speech = self._speech_provider
        speech_model = None if speech is None else getattr(speech, "model", None)
        speech_region = None if speech is None else getattr(speech, "region", None)
        return [
            llm("basic"),
            llm("enrich"),
            ProviderSettingView(
                role="speech",
                configured=speech is not None,
                model=speech_model if isinstance(speech_model, str) else None,
                endpoint_host=speech_region if isinstance(speech_region, str) else None,
                required_env_vars=["DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"],
            ),
        ]
