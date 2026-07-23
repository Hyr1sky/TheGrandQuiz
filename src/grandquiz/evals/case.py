"""按 kind 区分的严格 Eval Case Interface 与 YAML 配置解析。

每类 Case 只暴露自己的 setup 字段；配置经 Pydantic discriminated union 校验，未知 kind、枚举、
跨 kind 字段和缺少的必填字段都会在 solve 前 fail closed。
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.selection import Focus


def _empty_strs() -> list[str]:
    return []


@dataclass(frozen=True)
class PresetVerdict:
    """Assess 前置：通过 selector 给一个 item 预置判决。"""

    target: str
    verdict: VerdictLabel


def _empty_presets() -> list[PresetVerdict]:
    return []


@dataclass(frozen=True)
class QualityProfile:
    """显式选择的 Tier-2 rubric 与最小参考证据。"""

    rubric_id: str
    reference: str


@dataclass(frozen=True)
class IngestCase:
    """Ingest eval：只拥有抓取来源与审批选择。"""

    kind: ClassVar[Literal["ingest"]] = "ingest"
    id: str
    expected_events: list[str]
    source: Literal["ok", "boom", "web_replay"] = "ok"
    approval_keep: list[str] = field(default_factory=_empty_strs)

    @property
    def quality_profile(self) -> None:
        return None

    @property
    def quality_question(self) -> None:
        return None


@dataclass(frozen=True)
class AssessCase:
    """Assess eval：只拥有考核前置、作答与路由配置。"""

    kind: ClassVar[Literal["assess"]] = "assess"
    id: str
    expected_events: list[str]
    stocked: bool = True
    preset: list[PresetVerdict] = field(default_factory=_empty_presets)
    answer: str = "我的作答"
    verdict: VerdictLabel = "对"
    answers: list[str] = field(default_factory=_empty_strs)
    provider: Literal["default", "language_echo", "dedup"] = "default"
    language: str = "中文"
    focus: Focus = "mixed"
    fixture: Literal["single", "multi"] = "single"
    scope: list[str] = field(default_factory=_empty_strs)
    question_type: str | None = None

    @property
    def quality_profile(self) -> None:
        return None

    @property
    def quality_question(self) -> None:
        return None


@dataclass(frozen=True)
class ReactCase:
    """ReAct eval：只拥有对话、cassette、夹具与可选质量门。"""

    kind: ClassVar[Literal["react"]] = "react"
    id: str
    expected_events: list[str]
    user_messages: list[str]
    cassette: str
    answer: str = "我的作答"
    react_fixture: Literal["quiz", "grounded", "web_acquisition"] = "quiz"
    quality: QualityProfile | None = None

    @property
    def quality_profile(self) -> QualityProfile | None:
        return self.quality

    @property
    def quality_question(self) -> str | None:
        return self.user_messages[-1] if self.user_messages else None


Case = IngestCase | AssessCase | ReactCase


class _StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _PresetConfig(_StrictConfig):
    target: str
    verdict: VerdictLabel


def _empty_preset_configs() -> list[_PresetConfig]:
    return []


class _QualityConfig(_StrictConfig):
    rubric_id: str
    reference: str


class _IngestSetup(_StrictConfig):
    source: Literal["ok", "boom", "web_replay"] = "ok"
    approval_keep: list[str] = Field(default_factory=_empty_strs)


class _AssessSetup(_StrictConfig):
    stocked: bool = True
    preset: list[_PresetConfig] = Field(default_factory=_empty_preset_configs)
    answer: str = "我的作答"
    verdict: VerdictLabel = "对"
    answers: list[str] = Field(default_factory=_empty_strs)
    provider: Literal["default", "language_echo", "dedup"] = "default"
    language: str = "中文"
    focus: Focus = "mixed"
    fixture: Literal["single", "multi"] = "single"
    scope: list[str] = Field(default_factory=_empty_strs)
    question_type: str | None = None


class _ReactSetup(_StrictConfig):
    user_messages: list[str]
    cassette: str
    answer: str = "我的作答"
    fixture: Literal["quiz", "grounded", "web_acquisition"] = "quiz"
    quality: _QualityConfig | None = None


class _IngestEnvelope(_StrictConfig):
    id: str
    kind: Literal["ingest"]
    setup: _IngestSetup = Field(default_factory=_IngestSetup)
    expected_events: list[str]


class _AssessEnvelope(_StrictConfig):
    id: str
    kind: Literal["assess"]
    setup: _AssessSetup = Field(default_factory=_AssessSetup)
    expected_events: list[str]


class _ReactEnvelope(_StrictConfig):
    id: str
    kind: Literal["react"]
    setup: _ReactSetup
    expected_events: list[str]


_CaseEnvelope = Annotated[
    _IngestEnvelope | _AssessEnvelope | _ReactEnvelope,
    Field(discriminator="kind"),
]
_CASE_ADAPTER: TypeAdapter[_CaseEnvelope] = TypeAdapter(_CaseEnvelope)


def parse_case(raw: Any) -> Case:
    """把 YAML 原始值解析为一个严格的 per-kind Case。"""
    envelope = _CASE_ADAPTER.validate_python(raw)
    if isinstance(envelope, _AssessEnvelope):
        setup = envelope.setup
        return AssessCase(
            id=envelope.id,
            expected_events=envelope.expected_events,
            stocked=setup.stocked,
            preset=[
                PresetVerdict(target=preset.target, verdict=preset.verdict)
                for preset in setup.preset
            ],
            answer=setup.answer,
            verdict=setup.verdict,
            answers=setup.answers,
            provider=setup.provider,
            language=setup.language,
            focus=setup.focus,
            fixture=setup.fixture,
            scope=setup.scope,
            question_type=setup.question_type,
        )
    if isinstance(envelope, _ReactEnvelope):
        setup = envelope.setup
        quality = (
            QualityProfile(
                rubric_id=setup.quality.rubric_id,
                reference=setup.quality.reference,
            )
            if setup.quality is not None
            else None
        )
        return ReactCase(
            id=envelope.id,
            expected_events=envelope.expected_events,
            user_messages=setup.user_messages,
            cassette=setup.cassette,
            answer=setup.answer,
            react_fixture=setup.fixture,
            quality=quality,
        )
    setup = envelope.setup
    return IngestCase(
        id=envelope.id,
        expected_events=envelope.expected_events,
        source=setup.source,
        approval_keep=setup.approval_keep,
    )
