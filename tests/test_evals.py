"""M8 Eval Harness 测试——8 规则用例全绿 + 报告列（token / prompt 版本）+ ReplayMiss 硬失败。

harness 用与 test_assessment / test_ingest 相同的假 provider（canned JSON）驱动，独立于 cassette。
本文件是 eval harness 自身的确定性契约测试（harness 是 eval 机制、这里验证它可信）。
"""

import pytest

from grandquiz.evals.harness import load_cases, run_all, run_case, solve
from grandquiz.providers.base import Role
from grandquiz.providers.replay import Cassette, ReplayMiss, ReplayProvider

_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}


async def test_all_eight_cases_pass() -> None:
    reports = await run_all()
    assert len(reports) == 8
    failing = {r.case_id: r.failures for r in reports if not r.passed}
    assert failing == {}, f"有用例未通过：{failing}"


async def test_report_has_token_cost_and_prompt_version_columns() -> None:
    reports = {r.case_id: r for r in await run_all()}
    # 出题 / 判卷用例烧 token 且带 name@digest 形态的 prompt 版本。假 provider 每次 Usage(7+3)=10；
    # 断言精确值（非 >0），否则错误的汇总（如每调用计 1、或只算 prompt_tokens）能蒙混过关。
    case3 = reports["case3"]
    assert case3.total_tokens == 10  # MC：仅 1 次 enrich 出题；MC 判卷是代码、不烧 token
    assert case3.prompt_versions  # MC 出题至少落一条 prompt 版本
    assert all("@" in v for v in case3.prompt_versions), case3.prompt_versions
    # 追问用例既出题又判卷 → 两个不同 prompt 版本（question_probe + answer_grade）。
    case8 = reports["case8"]
    assert case8.total_tokens == 20  # enrich 出追问 + basic 判卷，各 (7+3)
    assert len(case8.prompt_versions) == 2, case8.prompt_versions
    # 空库拒答用例：0 token、无 prompt 版本（不调任何 LLM）。
    assert reports["case2"].total_tokens == 0
    assert reports["case2"].prompt_versions == []


async def test_replay_miss_is_a_hard_failure_never_silent_pass() -> None:
    # 空 cassette 的 ReplayProvider 注入 assess 用例 → provider 抛 ReplayMiss；runner 必须记为
    # 硬失败（passed=False + 捕获错误），绝不静默计为通过（决策 6）。
    case3 = next(c for c in load_cases() if c.id == "case3")
    report = await run_case(case3, provider_override=ReplayProvider(Cassette(), _MODELS))
    assert report.passed is False
    assert report.error is not None
    assert "ReplayMiss" in report.error


async def test_replay_miss_propagates_out_of_solve_uncaught() -> None:
    # solve 不吞 provider 异常（照既有编排语义原样冒泡）——保证硬失败不是被 solve 静默成 result。
    case3 = next(c for c in load_cases() if c.id == "case3")
    with pytest.raises(ReplayMiss):
        await solve(case3, provider_override=ReplayProvider(Cassette(), _MODELS))
