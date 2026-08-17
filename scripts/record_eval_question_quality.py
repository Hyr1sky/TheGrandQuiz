"""Record the owner-approved Question Quality Development Gold calibration.

Run only with explicit authorization for the five synthetic samples:

    uv run --env-file .env python scripts/record_eval_question_quality.py

This recording is development calibration evidence. It is not a holdout and cannot
authorize production feedback, prompt promotion, or automatic evolution.
"""

import asyncio

from grandquiz.evals.quality_calibration import CalibratedQualitySuite
from grandquiz.evals.quality_dataset import load_question_quality_development_gold
from grandquiz.evals.resources import (
    QUESTION_QUALITY_CALIBRATION_CASSETTE,
    eval_fixture_target,
)
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = eval_fixture_target(QUESTION_QUALITY_CALIBRATION_CASSETTE)


async def main() -> None:
    calibration = load_question_quality_development_gold()
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)
    try:
        suite = await CalibratedQualitySuite.create(
            provider=recording,
            rubric_id="question_quality",
            calibration=calibration,
        )
    finally:
        await provider.aclose()

    report = suite.calibration
    print(
        "question-quality Development Gold: "
        f"passed={report.passed} "
        f"agreement={report.agreement:.3f} "
        f"exact_agreement={report.exact_agreement:.3f} "
        f"judge_tokens={report.judge_tokens}"
    )
    print(f"pack={report.pack_id} sha256={report.pack_content_sha256}")
    for result in report.results:
        print(f"  {result.sample_id}: {'PASS' if result.passed else 'FAIL'}")
        for failure in result.failures:
            print(f"    - {failure}")
    if not report.passed:
        raise RuntimeError("Question Quality Development Gold calibration failed")

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)
    print(f"cassette saved: {_FIXTURE}")


if __name__ == "__main__":
    asyncio.run(main())
