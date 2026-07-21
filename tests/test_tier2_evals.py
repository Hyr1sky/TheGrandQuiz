"""Tier-2 QualitySuite 与现有 Tier-1 Harness 的端到端接缝。"""

import json
from collections.abc import Sequence
from typing import Any, cast

from grandquiz.evals.harness import load_cases, run_all, run_case
from grandquiz.evals.quality_calibration import CalibratedQualitySuite
from grandquiz.providers.base import Completion, Message, Role, Usage
from grandquiz.providers.replay import Cassette, ReplayProvider


class _CalibrationAwareProvider:
    """只模拟外部 LLM 边界；按公开 QualityRequest 生成结构化判定。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        self.calls += 1
        request = cast("dict[str, Any]", json.loads(messages[1].content))
        question = str(request["question"])
        candidate = str(request["candidate"])
        reference = str(request["reference"])
        if "不会发生故障" in candidate:
            scores = (1, 1, 1)
        elif "没有说明会加密" in candidate:
            scores = (4, 4, 4)
        elif "哪些消费者" in question and "trace" not in candidate:
            scores = (4, 2, 3)
        else:
            scores = (4, 4, 4)
        criteria: list[dict[str, object]] = []
        raw_criteria = cast("list[dict[str, str]]", request["criteria"])
        for item, score in zip(raw_criteria, scores, strict=True):
            criteria.append(
                {
                    "criterion_id": item["criterion_id"],
                    "score": score,
                    "rationale": "按人工标注边界判定。",
                    "candidate_evidence": candidate[:12],
                    "reference_evidence": reference[:12],
                }
            )
        return Completion(
            text=json.dumps(
                {
                    "rubric_id": request["rubric_id"],
                    "criteria": criteria,
                    "overall_rationale": "结构化质量判定。",
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=10, completion_tokens=5),
        )


async def test_case15_runs_only_after_calibration_and_separates_both_tiers() -> None:
    provider = _CalibrationAwareProvider()
    suite = await CalibratedQualitySuite.create(provider=provider)
    case15 = next(case for case in load_cases() if case.id == "case15")

    report = await run_case(case15, quality_suite=suite)

    assert suite.calibration.passed is True
    assert suite.calibration.agreement == 1.0
    assert report.rule_passed is True
    assert report.quality_passed is True
    assert report.passed is True
    assert report.total_tokens == 10_282
    assert report.execution_tokens == 10_282
    assert report.judge_tokens == 15
    assert report.judge_prompt_versions[0].startswith("quality_judge@")
    assert report.quality_evaluation is not None
    assert report.quality_evaluation.rubric_id == "grounded_answer"
    assert report.quality_events[0].type == "eval.quality_judge.started"
    assert report.quality_events[-1].type == "eval.quality_judge.ended"
    assert all(not event.type.startswith("eval.quality_judge") for event in report.subject_events)
    assert provider.calls == 5  # 4 calibration + 1 case15，不含 subject Replay 调用


async def test_quality_enabled_case_cannot_silently_fall_back_to_rule_only() -> None:
    case15 = next(case for case in load_cases() if case.id == "case15")

    report = await run_case(case15)

    assert report.rule_passed is True
    assert report.quality_passed is False
    assert report.quality_rubric_id == "grounded_answer"
    assert report.passed is False
    assert any("缺少已校准" in failure for failure in report.failures)


async def test_run_all_calibrates_once_and_only_judges_quality_cases() -> None:
    provider = _CalibrationAwareProvider()

    reports = await run_all(quality_provider_override=provider)

    assert len(reports) == 17
    assert all(report.passed for report in reports)
    assert provider.calls == 5  # 4 calibration + case15；其余 16 条不调用 judge


async def test_run_all_turns_quality_replay_miss_into_a_case_failure() -> None:
    replay = ReplayProvider(Cassette(), {"basic": "missing-quality-model"})

    reports = await run_all(quality_provider_override=replay)

    failures = {report.case_id: report.failures for report in reports if not report.passed}
    assert list(failures) == ["case15"]
    assert any("ReplayMiss" in failure for failure in failures["case15"])
