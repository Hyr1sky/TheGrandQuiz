"""CLI adapter for human-labelled production-grader calibration."""

import json
from pathlib import Path
from typing import Literal

import yaml

from grandquiz.domain.learning.eval_inbox import DatasetSnapshotV1
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.evals.grading_calibration import (
    CalibrationRunManifest,
    GradingCalibrationPolicy,
    GradingCalibrationReport,
    load_grading_calibration_samples,
    run_grading_calibration,
    run_snapshot_grading_calibration,
)
from grandquiz.evals.grading_dataset import (
    compile_grading_dataset,
    promote_grading_dataset,
)
from grandquiz.evals.grading_experiment import (
    GradingExperimentComparison,
    compare_grading_reports,
)
from grandquiz.providers.base import Provider, Role
from grandquiz.providers.llm import (
    OpenAICompatProvider,
    ReasoningEffort,
    RoleOverrides,
    ThinkingMode,
)
from grandquiz.providers.replay import Cassette, RecordingProvider


async def run_grading_calibration_cli(
    *,
    samples_path: Path,
    out_path: Path,
    min_samples: int,
    provider: Provider,
    run_manifest: CalibrationRunManifest | None = None,
) -> GradingCalibrationReport:
    report = await run_grading_calibration(
        load_grading_calibration_samples(samples_path),
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=min_samples),
        run_manifest=run_manifest,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{report.status}: {out_path}")
    return report


def prepare_grading_calibration_cli(
    *,
    pack_dir: Path,
    db_path: Path,
    out_dir: Path,
    reviewer: str,
    review_reason: str,
    request_id: str | None,
    now: float,
) -> DatasetSnapshotV1:
    """Compile and locally promote human labels without invoking an LLM."""

    compilation = compile_grading_dataset(pack_dir)
    effective_request_id = request_id or f"grading-calibration:{compilation.content_sha256}"
    with LearningPersistence(db_path) as persistence:
        snapshot = promote_grading_dataset(
            compilation,
            inbox=persistence.eval_inbox,
            request_id=effective_request_id,
            reviewer=reviewer,
            review_reason=review_reason,
            now=now,
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "compilation.json").write_text(
        json.dumps(compilation.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "grading-samples.yaml").write_text(
        yaml.safe_dump(
            [sample.model_dump(mode="json") for sample in compilation.samples],
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (out_dir / "dataset-snapshot.json").write_text(
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"prepared {snapshot.eligible_blind_count} eligible samples; "
        f"snapshot={snapshot.snapshot_id}"
    )
    return snapshot


def compare_grading_calibration_cli(
    *, report_paths: list[Path], out_path: Path
) -> GradingExperimentComparison:
    """Validate fixed-cohort identity and write one compact experiment comparison."""

    reports = [
        GradingCalibrationReport.model_validate_json(path.read_text(encoding="utf-8"))
        for path in report_paths
    ]
    comparison = compare_grading_reports(reports)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(comparison.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"compared {len(comparison.conditions)} conditions: {out_path}")
    return comparison


async def run_snapshot_grading_calibration_cli(
    *,
    snapshot_id: str,
    db_path: Path,
    out_path: Path,
    min_samples: int,
    provider: Provider,
    run_manifest: CalibrationRunManifest | None = None,
    sample_ids: list[str] | None = None,
) -> GradingCalibrationReport:
    """Run a reviewed local snapshot through the production grader."""

    with LearningPersistence(db_path) as persistence:
        snapshot = persistence.eval_inbox.require_snapshot(snapshot_id)
    report = await run_snapshot_grading_calibration(
        snapshot,
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=min_samples),
        run_manifest=run_manifest,
        sample_ids=sample_ids,
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
    model: str | None = None,
    thinking_mode: ThinkingMode | None = None,
    reasoning_effort: ReasoningEffort | None = None,
    cassette_path: Path | None = None,
) -> GradingCalibrationReport:
    provider, effective_provider, manifest = _live_calibration_provider(
        model=model,
        thinking_mode=thinking_mode,
        reasoning_effort=reasoning_effort,
        cassette_path=cassette_path,
    )
    try:
        return await run_grading_calibration_cli(
            samples_path=samples_path,
            out_path=out_path,
            min_samples=min_samples,
            provider=effective_provider,
            run_manifest=manifest,
        )
    finally:
        await provider.aclose()


async def run_live_snapshot_grading_calibration_cli(
    *,
    snapshot_id: str,
    db_path: Path,
    out_path: Path,
    min_samples: int,
    model: str | None = None,
    thinking_mode: ThinkingMode | None = None,
    reasoning_effort: ReasoningEffort | None = None,
    cassette_path: Path | None = None,
    sample_ids: list[str] | None = None,
) -> GradingCalibrationReport:
    provider, effective_provider, manifest = _live_calibration_provider(
        model=model,
        thinking_mode=thinking_mode,
        reasoning_effort=reasoning_effort,
        cassette_path=cassette_path,
    )
    try:
        return await run_snapshot_grading_calibration_cli(
            snapshot_id=snapshot_id,
            db_path=db_path,
            out_path=out_path,
            min_samples=min_samples,
            provider=effective_provider,
            run_manifest=manifest,
            sample_ids=sample_ids,
        )
    finally:
        await provider.aclose()


def _live_calibration_provider(
    *,
    model: str | None,
    thinking_mode: ThinkingMode | None,
    reasoning_effort: ReasoningEffort | None,
    cassette_path: Path | None,
) -> tuple[OpenAICompatProvider, Provider, CalibrationRunManifest]:
    override_effort: ReasoningEffort | Literal["none"] | None = reasoning_effort
    if thinking_mode == "disabled" and override_effort is None:
        override_effort = "none"
    provider = OpenAICompatProvider.from_env(
        role_overrides={
            "basic": RoleOverrides(
                model=model,
                thinking_mode=thinking_mode,
                reasoning_effort=override_effort,
            )
        }
    )
    execution = provider.execution_config_for_role["basic"]
    effective_provider: Provider = provider
    if cassette_path is not None:
        cassette = Cassette.load(cassette_path) if cassette_path.is_file() else Cassette()
        identities: dict[Role, str] = {
            role: config.replay_identity
            for role, config in provider.execution_config_for_role.items()
        }
        effective_provider = RecordingProvider(
            provider,
            cassette,
            identities,
            checkpoint_path=cassette_path,
            reuse_existing=True,
        )
    manifest = CalibrationRunManifest(
        provider=execution.provider,
        endpoint_host=execution.endpoint_host,
        model=execution.model,
        thinking_mode=execution.thinking_mode,
        reasoning_effort=execution.reasoning_effort,
    )
    return provider, effective_provider, manifest
