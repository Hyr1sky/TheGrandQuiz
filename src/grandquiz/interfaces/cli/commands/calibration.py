"""CLI adapter for human-labelled production-grader calibration."""

import json
from pathlib import Path

from grandquiz.evals.grading_calibration import (
    GradingCalibrationPolicy,
    GradingCalibrationReport,
    load_grading_calibration_samples,
    run_grading_calibration,
)
from grandquiz.providers.base import Provider
from grandquiz.providers.llm import OpenAICompatProvider


async def run_grading_calibration_cli(
    *,
    samples_path: Path,
    out_path: Path,
    min_samples: int,
    provider: Provider,
) -> GradingCalibrationReport:
    report = await run_grading_calibration(
        load_grading_calibration_samples(samples_path),
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=min_samples),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{report.status}: {out_path}")
    return report


async def run_live_grading_calibration_cli(
    *,
    samples_path: Path,
    out_path: Path,
    min_samples: int,
) -> GradingCalibrationReport:
    provider = OpenAICompatProvider.from_env()
    try:
        return await run_grading_calibration_cli(
            samples_path=samples_path,
            out_path=out_path,
            min_samples=min_samples,
            provider=provider,
        )
    finally:
        await provider.aclose()
