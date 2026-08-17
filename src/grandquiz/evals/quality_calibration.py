"""人工标注 calibration samples 对 Tier-2 QualityJudge 的信任门。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field

from grandquiz.evals.quality import QualityEvaluation, QualityJudge, QualityRequest
from grandquiz.evals.quality_contracts import CalibrationSample, ScoreRange
from grandquiz.evals.quality_dataset import CompiledQualityCalibration
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Provider

__all__ = ["CalibrationSample", "ScoreRange"]


class CalibrationSampleResult(BaseModel):
    sample_id: str
    passed: bool
    failures: list[str]


class CalibrationReport(BaseModel):
    schema_version: Literal["quality-calibration-report.v2"] = "quality-calibration-report.v2"
    rubric_id: str
    pack_id: str | None = None
    evidence_class: Literal["development_gold"] | None = None
    pack_content_sha256: str | None = None
    prompt_versions: tuple[str, ...]
    trace_id: str
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
    async def create(
        cls,
        *,
        provider: Provider,
        rubric_id: str = "grounded_answer",
        calibration: CompiledQualityCalibration | None = None,
    ) -> CalibratedQualitySuite:
        if calibration is None:
            if rubric_id != "grounded_answer":
                raise QualityCalibrationError(
                    f"{rubric_id} requires an owner-adjudicated calibration pack"
                )
            samples = load_calibration_samples()
        else:
            if calibration.rubric_id != rubric_id:
                raise QualityCalibrationError(f"compiled calibration does not target {rubric_id}")
            samples = list(calibration.samples)
        if not samples:
            raise QualityCalibrationError(f"calibration samples must target {rubric_id}")
        judge = QualityJudge(provider=provider)
        report = await run_calibration(samples, judge=judge, calibration=calibration)
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
    calibration: CompiledQualityCalibration | None = None,
) -> CalibrationReport:
    """运行全部人工 sample；任何维度越出人类区间即整体不可信。"""
    rubric_ids = {sample.rubric_id for sample in samples}
    if len(rubric_ids) != 1:
        raise QualityCalibrationError("calibration samples must target exactly one rubric")
    rubric_id = next(iter(rubric_ids))
    if calibration is not None and calibration.rubric_id != rubric_id:
        raise QualityCalibrationError("calibration provenance does not match its samples")
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
    prompt_versions: set[str] = set()
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
        prompt_versions.add(evaluation.prompt_version)
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
        rubric_id=rubric_id,
        pack_id=calibration.pack_id if calibration is not None else None,
        evidence_class=calibration.evidence_class if calibration is not None else None,
        pack_content_sha256=(calibration.content_sha256 if calibration is not None else None),
        prompt_versions=tuple(sorted(prompt_versions)),
        trace_id=emitter.trace_id,
        passed=bool(results) and all(result.passed for result in results),
        agreement=agreement,
        exact_agreement=exact_agreement,
        judge_tokens=judge_tokens,
        results=results,
        events=events,
    )
