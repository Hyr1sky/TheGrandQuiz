"""Versioned coverage manifest for the Evaluation Program."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from grandquiz.evals.case import EVAL_SURFACES, Case, EvalSurface

EvalTargetKind = Literal["case", "benchmark"]
EvalTier = Literal["tier1", "tier2", "benchmark"]


@dataclass(frozen=True)
class EvalCoverageTarget:
    """One executable case or separately governed benchmark."""

    target_id: str
    kind: EvalTargetKind
    tiers: tuple[EvalTier, ...]
    surfaces: tuple[EvalSurface, ...]


@dataclass(frozen=True)
class EvalCoverageReport:
    """A deterministic, versioned inventory of evaluated product surfaces."""

    schema_version: Literal["eval-coverage.v1"]
    surfaces: tuple[EvalSurface, ...]
    targets: tuple[EvalCoverageTarget, ...]
    uncovered_surfaces: tuple[EvalSurface, ...]


GRADING_BENCHMARK_TARGET = EvalCoverageTarget(
    target_id="grading-benchmark",
    kind="benchmark",
    tiers=("benchmark",),
    surfaces=("answer_grading",),
)


def build_coverage_report(cases: Sequence[Case]) -> EvalCoverageReport:
    """Build coverage without executing cases or benchmarks."""

    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Eval case ids must be unique")

    case_targets = tuple(
        EvalCoverageTarget(
            target_id=case.id,
            kind="case",
            tiers=("tier1", "tier2") if case.quality_profile is not None else ("tier1",),
            surfaces=case.surfaces,
        )
        for case in cases
    )
    targets = (*case_targets, GRADING_BENCHMARK_TARGET)
    covered = {surface for target in targets for surface in target.surfaces}
    return EvalCoverageReport(
        schema_version="eval-coverage.v1",
        surfaces=EVAL_SURFACES,
        targets=targets,
        uncovered_surfaces=tuple(surface for surface in EVAL_SURFACES if surface not in covered),
    )
