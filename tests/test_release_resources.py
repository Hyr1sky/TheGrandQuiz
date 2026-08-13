"""已安装 wheel 运行 ``grandquiz report`` 所需的包内资产契约。"""

from dataclasses import replace

import pytest

from grandquiz.evals.case import ReactCase
from grandquiz.evals.harness import load_cases
from grandquiz.evals.resources import (
    EVAL_FIXTURES_DIR,
    declared_eval_fixture_names,
    eval_fixture_path,
    eval_fixture_target,
)


def test_public_eval_cassettes_are_package_local() -> None:
    names = declared_eval_fixture_names(load_cases())

    assert {path.name for path in EVAL_FIXTURES_DIR.glob("*.json")} == names
    for name in names:
        path = eval_fixture_path(name)
        assert path.is_relative_to(EVAL_FIXTURES_DIR)
        assert path.stat().st_size > 0


def test_eval_fixture_path_rejects_escape() -> None:
    with pytest.raises(ValueError, match="无效"):
        eval_fixture_path("../tests/fixtures/secret.json")


def test_eval_fixture_target_allows_first_recording_but_rejects_escape() -> None:
    target = eval_fixture_target("future_case.cassette.json")

    assert target == EVAL_FIXTURES_DIR / "future_case.cassette.json"
    assert not target.exists()
    with pytest.raises(ValueError, match="无效"):
        eval_fixture_target("../future_case.cassette.json")


def test_eval_fixture_declarations_reject_multiple_owners() -> None:
    cases = load_cases()
    case14 = next(case for case in cases if case.id == "case14")
    case15 = next(case for case in cases if case.id == "case15")
    assert isinstance(case14, ReactCase)
    assert isinstance(case15, ReactCase)

    with pytest.raises(ValueError, match="multiple owners"):
        declared_eval_fixture_names(
            [
                case14,
                replace(case15, cassette=case14.cassette),
            ]
        )
