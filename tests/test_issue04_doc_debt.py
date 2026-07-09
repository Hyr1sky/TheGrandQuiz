"""Issue 04 回归探针——文档债 + 清遗留 repl.py。

纯确定性断言（不触 LLM / 网络）：守住三条易回退的事实——
1. evals 包 docstring 的用例条数与磁盘上 ``cases/*.yaml`` 实数一致（不再写死"8 条"）。
2. docstring 明确把 Tier-2 LLM judge 标为待建 / scoped-out（当前只兑现 Tier-1 规则断言）。
3. 遗留的 M1 回声 REPL（``interfaces/cli/repl.py``）已删除（入口是 app:main）。
"""

from pathlib import Path

import grandquiz.evals as evals_pkg
from grandquiz.evals.harness import load_cases

_PKG_DIR = Path(evals_pkg.__file__).parent  # .../grandquiz/evals


def test_evals_docstring_case_count_matches_disk() -> None:
    doc = evals_pkg.__doc__ or ""
    n = len(load_cases())
    assert n == 13  # 8 既有 + case9 语言 / case10 去重 + GKB-S7 case11/12/13
    assert f"{n} 条" in doc, doc
    assert "8 条" not in doc  # 旧写死的条数残留必须清掉


def test_evals_docstring_marks_tier2_as_scoped_out() -> None:
    doc = evals_pkg.__doc__ or ""
    # 只兑现 Tier-1 规则断言；Tier-2 LLM judge 须显式标为待建 / scoped-out，不暗示已双 Tier。
    assert "Tier-2" in doc
    assert ("待建" in doc) or ("scoped-out" in doc)


def test_legacy_repl_module_is_deleted() -> None:
    repl = _PKG_DIR.parent / "interfaces" / "cli" / "repl.py"
    assert not repl.exists(), repl
