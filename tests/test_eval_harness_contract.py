"""Stable facade and suite manifest for the Eval Harness refactor."""

from grandquiz.evals.harness import (
    CaseReport,
    export_html_report,
    load_cases,
    render_report,
    run_all,
    run_case,
    solve,
)


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
