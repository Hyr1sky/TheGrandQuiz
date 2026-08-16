"""Stable facade and suite manifest for the Eval Harness refactor."""

from dataclasses import replace

import pytest

from grandquiz.evals.case import AssessCase, IngestCase, ReactCase
from grandquiz.evals.harness import (
    AssessObservation,
    BasicIngestObservation,
    CaseReport,
    ReactObservation,
    WebAcquisitionObservation,
    describe_coverage,
    export_html_report,
    load_cases,
    render_report,
    run_all,
    run_case,
    solve,
)


def test_eval_program_describes_case_and_benchmark_surface_coverage() -> None:
    report = describe_coverage()

    assert report.schema_version == "eval-coverage.v1"
    assert list(report.surfaces) == [
        "acquisition",
        "reader_grounding",
        "grounded_answer",
        "question_generation",
        "answer_grading",
        "learning_state_transition",
    ]
    assert [
        (target.target_id, target.kind, target.tiers, target.surfaces) for target in report.targets
    ] == [
        ("case1", "case", ("tier1",), ("reader_grounding",)),
        ("case10", "case", ("tier1",), ("question_generation",)),
        ("case11", "case", ("tier1",), ("question_generation",)),
        ("case12", "case", ("tier1",), ("question_generation",)),
        ("case13", "case", ("tier1",), ("question_generation", "answer_grading")),
        ("case14", "case", ("tier1",), ("question_generation",)),
        (
            "case15",
            "case",
            ("tier1", "tier2"),
            ("reader_grounding", "grounded_answer"),
        ),
        ("case16", "case", ("tier1",), ("acquisition", "reader_grounding")),
        ("case17", "case", ("tier1",), ("acquisition", "reader_grounding")),
        ("case2", "case", ("tier1",), ("question_generation",)),
        ("case3", "case", ("tier1",), ("question_generation",)),
        (
            "case4",
            "case",
            ("tier1",),
            ("answer_grading", "learning_state_transition"),
        ),
        (
            "case5",
            "case",
            ("tier1",),
            ("question_generation", "learning_state_transition"),
        ),
        (
            "case6",
            "case",
            ("tier1",),
            ("answer_grading", "learning_state_transition"),
        ),
        ("case7", "case", ("tier1",), ("acquisition",)),
        ("case8", "case", ("tier1",), ("question_generation", "answer_grading")),
        ("case9", "case", ("tier1",), ("question_generation",)),
        ("grading-benchmark", "benchmark", ("benchmark",), ("answer_grading",)),
    ]
    assert report.uncovered_surfaces == ()


def test_eval_program_rejects_duplicate_case_ownership_before_execution() -> None:
    cases = load_cases()
    duplicate = replace(cases[0], id=cases[1].id)

    with pytest.raises(ValueError, match="case ids must be unique"):
        describe_coverage(cases=(*cases, duplicate))


def test_eval_harness_facade_and_case_manifest_remain_stable() -> None:
    assert all(
        callable(entry)
        for entry in (
            export_html_report,
            load_cases,
            render_report,
            run_all,
            run_case,
            solve,
        )
    )
    assert CaseReport.__name__ == "CaseReport"
    assert [
        (
            case.id,
            case.kind,
            None if case.quality_profile is None else case.quality_profile.rubric_id,
        )
        for case in load_cases()
    ] == [
        ("case1", "ingest", None),
        ("case10", "assess", None),
        ("case11", "assess", None),
        ("case12", "assess", None),
        ("case13", "assess", None),
        ("case14", "react", None),
        ("case15", "react", "grounded_answer"),
        ("case16", "ingest", None),
        ("case17", "react", None),
        ("case2", "assess", None),
        ("case3", "assess", None),
        ("case4", "assess", None),
        ("case5", "assess", None),
        ("case6", "assess", None),
        ("case7", "ingest", None),
        ("case8", "assess", None),
        ("case9", "assess", None),
    ]


async def test_solve_returns_a_typed_observation_for_each_case_kind() -> None:
    cases = {case.id: case for case in load_cases()}

    assess = await solve(cases["case3"])
    ingest = await solve(cases["case1"])
    acquisition = await solve(cases["case16"])
    react = await solve(cases["case14"])

    assert isinstance(assess.case, AssessCase)
    assert isinstance(assess.observation, AssessObservation)
    assert isinstance(ingest.case, IngestCase)
    assert isinstance(ingest.observation, BasicIngestObservation)
    assert isinstance(acquisition.case, IngestCase)
    assert isinstance(acquisition.observation, WebAcquisitionObservation)
    assert isinstance(react.case, ReactCase)
    assert isinstance(react.observation, ReactObservation)
    assert not hasattr(assess, "context")


async def test_solve_result_rejects_an_observation_owned_by_another_case_kind() -> None:
    assess = await solve(next(case for case in load_cases() if case.id == "case3"))

    with pytest.raises(TypeError, match="AssessCase requires AssessObservation"):
        replace(
            assess,
            observation=ReactObservation(
                final_outputs=(),
                grounded_resource_id=None,
                full_document_chars=0,
            ),
        )
