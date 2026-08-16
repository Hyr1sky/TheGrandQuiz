"""Stable facade for the Eval Harness.

Callers keep one small interface while execution, suite policy, and reporting evolve
behind separate deep Modules.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from grandquiz.evals.case import Case
from grandquiz.evals.coverage import EvalCoverageReport, build_coverage_report
from grandquiz.evals.fixture import MC_CORRECT, MC_WRONG, READER_JSON
from grandquiz.evals.reporting import export_reports_html as _export_reports_html
from grandquiz.evals.reporting import render_report
from grandquiz.evals.result import (
    AssessObservation,
    BasicIngestObservation,
    CaseReport,
    ReactObservation,
    SolveResult,
    WebAcquisitionObservation,
)
from grandquiz.evals.runner import run_all, run_case
from grandquiz.evals.solvers import (
    QUOTES,
    AssessFakeProvider,
    build_event_harness,
    build_stocked_store,
    load_cases,
    solve,
    summarize_spans,
)

__all__ = [
    "MC_CORRECT",
    "MC_WRONG",
    "QUOTES",
    "READER_JSON",
    "AssessFakeProvider",
    "AssessObservation",
    "BasicIngestObservation",
    "Case",
    "CaseReport",
    "EvalCoverageReport",
    "ReactObservation",
    "SolveResult",
    "WebAcquisitionObservation",
    "build_event_harness",
    "build_stocked_store",
    "describe_coverage",
    "export_html_report",
    "load_cases",
    "main",
    "render_report",
    "run_all",
    "run_case",
    "solve",
    "summarize_spans",
]


def describe_coverage(*, cases: Sequence[Case] | None = None) -> EvalCoverageReport:
    """Describe the versioned Evaluation Program without executing it."""

    return build_coverage_report(load_cases() if cases is None else cases)


def main() -> int:
    """Run the offline suite; process success means every case passed."""

    reports = asyncio.run(run_all())
    print(render_report(reports))
    return 0 if all(report.passed for report in reports) else 1


async def export_html_report(out_dir: Path) -> Path:
    """Run the offline suite and export its self-contained report artifacts."""

    return _export_reports_html(await run_all(), out_dir)
