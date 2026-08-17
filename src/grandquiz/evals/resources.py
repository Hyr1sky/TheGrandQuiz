"""Eval 的只读发布资产定位。

公开的 ``grandquiz report`` 必须能从已安装 wheel 离线运行，因此运行时 cassette 必须随
``grandquiz.evals`` 一起分发，不能依赖仓库根目录或 ``tests/``。
"""

from collections.abc import Iterable
from pathlib import Path

from grandquiz.evals.case import Case, IngestCase, ReactCase

EVAL_FIXTURES_DIR = Path(__file__).parent / "fixtures"
QUALITY_EVAL_CASSETTE = "eval_quality_grounded_answer.cassette.json"
QUESTION_QUALITY_CALIBRATION_CASSETTE = "eval_quality_question_development_gold.cassette.json"
READER_FIDELITY_CALIBRATION_CASSETTE = "eval_quality_reader_fidelity_development_gold.cassette.json"
GROUNDED_ANSWER_SLICES_CALIBRATION_CASSETTE = (
    "eval_quality_grounded_answer_slices_development_gold.cassette.json"
)
SUITE_EVAL_FIXTURES = frozenset(
    {
        QUALITY_EVAL_CASSETTE,
        QUESTION_QUALITY_CALIBRATION_CASSETTE,
        READER_FIDELITY_CALIBRATION_CASSETTE,
        GROUNDED_ANSWER_SLICES_CALIBRATION_CASSETTE,
    }
)


def declared_eval_fixture_names(cases: Iterable[Case]) -> set[str]:
    """Return every case- or suite-owned packaged eval fixture name."""
    owners = {name: "suite" for name in SUITE_EVAL_FIXTURES}

    def claim(name: str, owner: str) -> None:
        previous = owners.get(name)
        if previous is not None:
            raise ValueError(f"eval fixture {name!r} has multiple owners: {previous}, {owner}")
        owners[name] = owner

    for case in cases:
        if isinstance(case, ReactCase):
            claim(case.cassette, f"case:{case.id}:llm")
        if isinstance(case, IngestCase | ReactCase) and case.acquisition_replay is not None:
            claim(case.acquisition_replay.cassette, f"case:{case.id}:acquisition")
    return set(owners)


def eval_fixture_path(name: str) -> Path:
    """返回包内 cassette；拒绝路径穿越和缺失资产。"""
    path = eval_fixture_target(name)
    if not path.is_file():
        raise FileNotFoundError(f"Eval fixture 未随安装包分发：{name}")
    return path


def eval_fixture_target(name: str) -> Path:
    """Return a validated package-local target for an explicit recording script."""
    candidate = Path(name)
    if candidate.name != name or candidate.suffix != ".json":
        raise ValueError(f"无效的 eval fixture 名称：{name}")
    return EVAL_FIXTURES_DIR / name
