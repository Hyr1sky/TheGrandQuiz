"""人工标注 calibration samples 对 Tier-2 QualityJudge 的信任门。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field, model_validator

from grandquiz.evals.quality import QualityEvaluation, QualityJudge, QualityRequest
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Provider


class ScoreRange(BaseModel):
    min: int = Field(ge=1, le=4)
    max: int = Field(ge=1, le=4)

    @model_validator(mode="after")
    def _ordered(self) -> ScoreRange:
        if self.min > self.max:
            raise ValueError("score range 的 min 不能大于 max")
        return self


class CalibrationSample(BaseModel):
    sample_id: str = Field(min_length=1)
    rubric_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    expected_scores: dict[str, ScoreRange] = Field(min_length=1)


class CalibrationSampleResult(BaseModel):
    sample_id: str
    passed: bool
    failures: list[str]


class CalibrationReport(BaseModel):
    passed: bool
    agreement: float = Field(ge=0.0, le=1.0)
    exact_agreement: float = Field(ge=0.0, le=1.0)
    judge_tokens: int = Field(ge=0)
    results: list[CalibrationSampleResult]
    events: list[AgentEvent]


class QualityCalibrationError(ValueError):
    """真实或 Replay judge 未通过人工 calibration gate。"""


class CalibratedQualitySuite:
    """只有成功复现人工边界后才暴露 evaluate 的 judge suite。"""

    def __init__(self, *, judge: QualityJudge, calibration: CalibrationReport) -> None:
        self._judge = judge
        self.calibration = calibration

    @classmethod
    async def create(cls, *, provider: Provider) -> CalibratedQualitySuite:
        judge = QualityJudge(provider=provider)
        report = await run_calibration(load_calibration_samples(), judge=judge)
        if not report.passed:
            failed_ids = [result.sample_id for result in report.results if not result.passed]
            raise QualityCalibrationError(
                f"quality judge 未通过人工 calibration：{', '.join(failed_ids)}"
            )
        return cls(judge=judge, calibration=report)

    async def evaluate(
        self,
        request: QualityRequest,
        *,
        emitter: EventEmitter,
    ) -> QualityEvaluation:
        return await self._judge.evaluate(request, emitter=emitter)


_CALIBRATION_PATH = Path(__file__).parent / "quality_cases" / "grounded_answer.yaml"


def load_calibration_samples() -> list[CalibrationSample]:
    """加载项目内人工标注的固定 calibration set。"""
    raw: Any = yaml.safe_load(_CALIBRATION_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("quality calibration 文件顶层必须是列表")
    return [CalibrationSample.model_validate(item) for item in cast("list[Any]", raw)]


async def run_calibration(
    samples: list[CalibrationSample],
    *,
    judge: QualityJudge,
) -> CalibrationReport:
    """运行全部人工 sample；任何维度越出人类区间即整体不可信。"""
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="quality-calibration")
    results: list[CalibrationSampleResult] = []
    agreed = 0
    total = 0
    exact_agreed = 0
    exact_total = 0
    judge_tokens = 0
    for sample in samples:
        evaluation = await judge.evaluate(
            QualityRequest(
                rubric_id=sample.rubric_id,
                question=sample.question,
                candidate=sample.candidate,
                reference=sample.reference,
            ),
            emitter=emitter,
        )
        judge_tokens += evaluation.usage.total_tokens
        failures: list[str] = []
        for criterion in evaluation.criteria:
            total += 1
            expected = sample.expected_scores.get(criterion.criterion_id)
            if expected is None:
                failures.append(f"缺少人工区间：{criterion.criterion_id}")
                continue
            if expected.min <= criterion.score <= expected.max:
                agreed += 1
            else:
                failures.append(
                    f"{criterion.criterion_id}={criterion.score} 不在人类区间 "
                    f"{expected.min}..{expected.max}"
                )
            if expected.min == expected.max:
                exact_total += 1
                if criterion.score == expected.min:
                    exact_agreed += 1
        results.append(
            CalibrationSampleResult(
                sample_id=sample.sample_id,
                passed=not failures,
                failures=failures,
            )
        )
    agreement = agreed / total if total else 0.0
    exact_agreement = exact_agreed / exact_total if exact_total else 0.0
    return CalibrationReport(
        passed=bool(results) and all(result.passed for result in results),
        agreement=agreement,
        exact_agreement=exact_agreement,
        judge_tokens=judge_tokens,
        results=results,
        events=events,
    )
