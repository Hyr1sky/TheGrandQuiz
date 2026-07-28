"""Eval 的只读发布资产定位。

公开的 ``grandquiz report`` 必须能从已安装 wheel 离线运行，因此运行时 cassette 必须随
``grandquiz.evals`` 一起分发，不能依赖仓库根目录或 ``tests/``。
"""

from pathlib import Path

EVAL_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def eval_fixture_path(name: str) -> Path:
    """返回包内 cassette；拒绝路径穿越和缺失资产。"""
    candidate = Path(name)
    if candidate.name != name or candidate.suffix != ".json":
        raise ValueError(f"无效的 eval fixture 名称：{name}")
    path = EVAL_FIXTURES_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Eval fixture 未随安装包分发：{name}")
    return path
