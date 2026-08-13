"""Stable facade for the Eval Harness.

Callers keep one small interface while execution, suite policy, and reporting evolve
behind separate deep Modules.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from grandquiz.evals.case import Case
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
    CASE17_FETCH_FINGERPRINT,
    CASE17_FETCH_NORMALIZATION,
    CASE17_SEARCH_FINGERPRINT,
    QUOTES,
    AssessFakeProvider,
    build_event_harness,
    build_stocked_store,
    load_cases,
    solve,
    summarize_spans,
)

__all__ = [
    "CASE17_FETCH_FINGERPRINT",
    "CASE17_FETCH_NORMALIZATION",
    "CASE17_SEARCH_FINGERPRINT",
    "MC_CORRECT",
    "MC_WRONG",
    "QUOTES",
    "READER_JSON",
    "AssessFakeProvider",
    "AssessObservation",
    "BasicIngestObservation",
    "Case",
    "CaseReport",
    "ReactObservation",
    "SolveResult",
    "WebAcquisitionObservation",
    "build_event_harness",
    "build_stocked_store",
    "export_html_report",
    "load_cases",
    "main",
    "render_report",
    "run_all",
    "run_case",
    "solve",
    "summarize_spans",
]


def main() -> int:
    """Run the offline suite; process success means every case passed."""

    reports = asyncio.run(run_all())
    print(render_report(reports))
    return 0 if all(report.passed for report in reports) else 1


async def export_html_report(out_dir: Path) -> Path:
    """Run the offline suite and export its self-contained report artifacts."""

    return _export_reports_html(await run_all(), out_dir)
