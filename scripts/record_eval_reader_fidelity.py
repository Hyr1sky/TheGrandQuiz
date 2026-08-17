"""Record the owner-approved Reader Fidelity Development Gold calibration.

Run only with explicit authorization for the five synthetic samples:

    uv run --env-file .env python scripts/record_eval_reader_fidelity.py

This recording is development calibration evidence. It is not a holdout and cannot
authorize production feedback, prompt promotion, or automatic evolution.
"""

import asyncio
from pathlib import Path

from grandquiz.evals.quality_calibration import CalibratedQualitySuite
from grandquiz.evals.quality_dataset import load_reader_fidelity_development_gold
from grandquiz.evals.resources import (
    READER_FIDELITY_CALIBRATION_CASSETTE,
    eval_fixture_target,
)
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = eval_fixture_target(READER_FIDELITY_CALIBRATION_CASSETTE)
_CHECKPOINT = Path(".scratch/eval-guided-evolution/reader-fidelity.recording.json")


async def main() -> None:
    calibration = load_reader_fidelity_development_gold()
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(
        provider,
        cassette,
        provider.model_for_role,
        checkpoint_path=_CHECKPOINT,
    )
    try:
        suite = await CalibratedQualitySuite.create(
            provider=recording,
            rubric_id="reader_fidelity",
            calibration=calibration,
        )
    finally:
        await provider.aclose()

    report = suite.calibration
    print(
        "Reader Fidelity Development Gold: "
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
        raise RuntimeError("Reader Fidelity Development Gold calibration failed")

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)
    print(f"cassette saved: {_FIXTURE}")


if __name__ == "__main__":
    asyncio.run(main())
