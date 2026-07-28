"""显式录制 Tier-2 calibration + case15 quality cassette。

运行：uv run --env-file .env python scripts/record_eval_quality.py

只有四个人工 calibration samples 全部通过、且 case15 的双层门均通过时才写 fixture。
默认 ``grandquiz report`` 永远只读取该 cassette，不会隐式访问外部模型。
"""

import asyncio

from grandquiz.evals.harness import load_cases, run_case
from grandquiz.evals.quality import QualityJudge
from grandquiz.evals.quality_calibration import (
    CalibratedQualitySuite,
    load_calibration_samples,
    run_calibration,
)
from grandquiz.evals.resources import eval_fixture_path
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = eval_fixture_path("eval_quality_grounded_answer.cassette.json")


async def main() -> None:
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)
    try:
        judge = QualityJudge(provider=recording)
        calibration = await run_calibration(load_calibration_samples(), judge=judge)
        print(
            "calibration: "
            f"passed={calibration.passed} "
            f"agreement={calibration.agreement:.3f} "
            f"exact_agreement={calibration.exact_agreement:.3f} "
            f"judge_tokens={calibration.judge_tokens}"
        )
        for result in calibration.results:
            print(f"  {result.sample_id}: {'PASS' if result.passed else 'FAIL'}")
            for failure in result.failures:
                print(f"    - {failure}")
        if not calibration.passed:
            raise RuntimeError("真实 calibration 未通过；拒绝运行 case15 或保存 cassette")
        suite = CalibratedQualitySuite(judge=judge, calibration=calibration)
        case15 = next(case for case in load_cases() if case.id == "case15")
        report = await run_case(case15, quality_suite=suite)
    finally:
        await provider.aclose()

    print(
        f"case15: rule={report.rule_passed} quality={report.quality_passed} "
        f"judge_tokens={report.judge_tokens}"
    )
    if report.quality_evaluation is not None:
        for criterion in report.quality_evaluation.criteria:
            print(f"  {criterion.criterion_id}={criterion.score}: {criterion.rationale}")
    if not suite.calibration.passed or not report.passed:
        raise RuntimeError("真实 calibration 或 case15 未通过；拒绝保存 cassette")

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)
    print(f"cassette 已存：{_FIXTURE}")


if __name__ == "__main__":
    asyncio.run(main())
