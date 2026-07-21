"""Issue 04 回归探针——文档债 + 清遗留 repl.py。

纯确定性断言（不触 LLM / 网络）：守住三条易回退的事实——
1. evals 包 docstring 的用例条数与磁盘上 ``cases/*.yaml`` 实数一致（不再写死"8 条"）。
2. docstring 诚实描述 Tier-2 已落地范围（只评 case15，其余用例仍是 Tier-1）。
3. 遗留的 M1 回声 REPL（``interfaces/cli/repl.py``）已删除（入口是 app:main）。
"""

from pathlib import Path

import grandquiz.evals as evals_pkg
from grandquiz.evals.harness import load_cases

_PKG_DIR = Path(evals_pkg.__file__).parent  # .../grandquiz/evals


def test_evals_docstring_case_count_matches_disk() -> None:
    doc = evals_pkg.__doc__ or ""
    n = len(load_cases())
    # 8 既有 + case9/10 + GKB-S7 case11/12/13 + react case14/15 + acquisition case16。
    assert n == 16
    assert f"{n} 条" in doc, doc
    assert "8 条" not in doc  # 旧写死的条数残留必须清掉


def test_evals_docstring_describes_the_limited_tier2_scope() -> None:
    doc = evals_pkg.__doc__ or ""
    assert "Tier-2" in doc
    assert "case15" in doc
    assert "15 条用例继续只跑 Tier-1" in doc


def test_legacy_repl_module_is_deleted() -> None:
    repl = _PKG_DIR.parent / "interfaces" / "cli" / "repl.py"
    assert not repl.exists(), repl
