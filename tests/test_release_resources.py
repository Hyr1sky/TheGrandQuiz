"""已安装 wheel 运行 ``grandquiz report`` 所需的包内资产契约。"""

import pytest

from grandquiz.evals.resources import EVAL_FIXTURES_DIR, eval_fixture_path


def test_public_eval_cassettes_are_package_local() -> None:
    names = {
        "eval_case14_bulk_quiz.cassette.json",
        "eval_case15_natural_grounded_answer.cassette.json",
        "eval_case16_web_acquisition.cassette.json",
        "eval_case17_web_acquisition.cassette.json",
        "eval_case17_web_acquisition_react.cassette.json",
        "eval_quality_grounded_answer.cassette.json",
    }

    assert {path.name for path in EVAL_FIXTURES_DIR.glob("*.json")} == names
    for name in names:
        path = eval_fixture_path(name)
        assert path.is_relative_to(EVAL_FIXTURES_DIR)
        assert path.stat().st_size > 0


def test_eval_fixture_path_rejects_escape() -> None:
    with pytest.raises(ValueError, match="无效"):
        eval_fixture_path("../tests/fixtures/secret.json")
