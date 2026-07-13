"""M8 Eval Harness 测试——8 规则用例全绿 + 报告列（token / prompt 版本）+ ReplayMiss 硬失败。

harness 用与 test_assessment / test_ingest 相同的假 provider（canned JSON）驱动，独立于 cassette。
本文件是 eval harness 自身的确定性契约测试（harness 是 eval 机制、这里验证它可信）。
"""

import pytest

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.graders.rules import grade_case14
from grandquiz.evals.graders.scorers import language_consistency, no_duplicate
from grandquiz.evals.harness import Case, SolveResult, load_cases, run_all, run_case, solve
from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.providers.base import Role
from grandquiz.providers.replay import Cassette, ReplayMiss, ReplayProvider

_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}


async def test_all_cases_pass() -> None:
    reports = await run_all()
    # 10（8 + 语言一致性 / 无重复）+ 3 GKB-S7（scope-honor / empty_scope / 题型 honor）+ 1 react
    # 层（case14，R2 首个：大批量出题不能编造）。
    assert len(reports) == 14
    failing = {r.case_id: r.failures for r in reports if not r.passed}
    assert failing == {}, f"有用例未通过：{failing}"


async def test_language_consistency_case_is_all_one_bucket() -> None:
    # 语言一致性用例（case9，英文 task、多轮）：跑在 run_all 里为绿，且直接调 scorer 复核——
    # 每题 question / options 都落英文桶、全会话同桶（跨轮语言不漂移）。
    reports = {r.case_id: r for r in await run_all()}
    assert reports["case9"].passed, reports["case9"].failures
    sr = await solve(next(c for c in load_cases() if c.id == "case9"))
    asked = [e for e in sr.events if e.type == LearningEvent.QUESTION_ASKED]
    assert len(asked) == 2  # 多轮 assess（≥2 轮）
    assert language_consistency(sr, "en") == []  # 全桶一致（en），无漂移


async def test_no_duplicate_case_has_no_verbatim_repeat() -> None:
    # 无重复用例（case10，复考同一薄弱 item 两轮）：跑在 run_all 里为绿，且直接调 scorer 复核——
    # 会话内零逐字重复，且两轮都锁定同一薄弱 item（薄弱优先未破）。
    reports = {r.case_id: r for r in await run_all()}
    assert reports["case10"].passed, reports["case10"].failures
    sr = await solve(next(c for c in load_cases() if c.id == "case10"))
    asked = [e for e in sr.events if e.type == LearningEvent.QUESTION_ASKED]
    assert len(asked) == 2
    weak_target = sr.context["weak_target"]
    assert all(e.payload["item_id"] == weak_target for e in asked)  # 薄弱优先未破
    assert no_duplicate(sr) == []  # 会话内零逐字重复


async def test_scope_honor_asks_only_within_scope() -> None:
    # scope-honor（case11，GKB-S7）：多资源夹具、scope=[资源A] → 所有出题 item 属 A，绝不串到
    # 资源 B（B 在库但被 exact-id 过滤排除）。跑在 run_all 里为绿，且直接复核事件轨迹。
    reports = {r.case_id: r for r in await run_all()}
    assert reports["case11"].passed, reports["case11"].failures
    sr = await solve(next(c for c in load_cases() if c.id == "case11"))
    resource_ids = set(sr.context["resource_ids"])
    pool_resources = {it.resource_id for it in sr.context["items"]}
    assert len(pool_resources) >= 2  # 多资源夹具（≥2 资源）
    assert resource_ids < pool_resources  # 资源 B 在库但被 scope 排除
    id_to_resource = {it.item_id: it.resource_id for it in sr.context["items"]}
    asked = [e for e in sr.events if e.type == LearningEvent.QUESTION_ASKED]
    assert asked
    assert all(id_to_resource[e.payload["item_id"]] in resource_ids for e in asked)


async def test_empty_scope_refuses_without_calling_provider() -> None:
    # empty_scope（case12，GKB-S7）：scope 无匹配 → ASSESSMENT_REFUSED(empty_scope)、零出题、
    # 零判卷（不调 provider）；与 case2 的 empty_kb 分野（库非空、仅 scope 命中为空）。
    reports = {r.case_id: r for r in await run_all()}
    assert reports["case12"].passed, reports["case12"].failures
    sr = await solve(next(c for c in load_cases() if c.id == "case12"))
    refused = next(e for e in sr.events if e.type == LearningEvent.ASSESSMENT_REFUSED)
    assert refused.payload["reason"] == "empty_scope"
    assert sr.calls == 0  # 不调任何 provider（零出题 / 零判卷）
    assert not [e for e in sr.events if e.type == LearningEvent.QUESTION_ASKED]
    assert len(sr.context["items"]) >= 1  # 库非空（否则应为 empty_kb）


async def test_question_type_intent_overrides_adaptive_routing() -> None:
    # question_type-honor（case13，GKB-S5/S7）：fresh item 自适应本会给选择题（routed），但用户
    # 显式"简答" → effective=开放（不出选择题、无 options）。护栏可回归：短答意图 ↛ 选择题。
    reports = {r.case_id: r for r in await run_all()}
    assert reports["case13"].passed, reports["case13"].failures
    sr = await solve(next(c for c in load_cases() if c.id == "case13"))
    asked = next(e for e in sr.events if e.type == LearningEvent.QUESTION_ASKED)
    assert asked.payload["routed"] == "选择题"  # 自适应本会出选择题
    assert asked.payload["effective"] == "开放"  # "简答"意图盖过 → 开放
    assert "options" not in asked.payload  # 不出选择题


async def test_case14_react_layer_calls_start_quiz_with_matching_count() -> None:
    # react 层用例（R2 首个）：真录 cassette 驱动 Runner.run_agent_turn，断言加固后的
    # react_system.md 真的让模型调用了 start_quiz(count=3)，而非在最终文本里编结果。
    reports = {r.case_id: r for r in await run_all()}
    assert reports["case14"].passed, reports["case14"].failures
    sr = await solve(next(c for c in load_cases() if c.id == "case14"))
    starts = [e for e in sr.events if e.type == EventType.TOOL_CALL_STARTED]
    assert len(starts) == 1
    assert starts[0].payload["tool_name"] == "start_quiz"
    assert starts[0].payload["arguments"]["count"] == 3
    asked = [e for e in sr.events if e.type == LearningEvent.QUESTION_ASKED]
    assert len(asked) == 3  # 真跑了 3 轮，不是编的


def _fake_case14() -> Case:
    return Case(
        id="case14",
        kind="react",
        expected_events=[],
        user_messages=["帮我出3道选择题"],
        cassette="x",
    )


def _fake_solve_result(events: list[AgentEvent]) -> SolveResult:
    return SolveResult(
        case=_fake_case14(),
        events=events,
        spans=[],
        result=None,
        store=LearningStore(),
        memory=LearningMemory(),
        calls=0,
        roles=[],
        context={},
    )


def _event(seq: int, etype: str, payload: dict[str, object]) -> AgentEvent:
    return AgentEvent(type=etype, seq=seq, ts=0.0, trace_id="fake", payload=payload)


def test_grade_case14_catches_zero_tool_call_fabrication() -> None:
    # 钉死 2026-07-12 dogfood 抓到的真实回归形状：模型全程零 tool_call，直接在最终文本里编结果。
    # 这个 SolveResult 是手造的假态（不依赖 cassette）——证明 grader 本身真能抓住这个失败模式，
    # 不只是"恰好这份录制的 cassette 是好的"。
    events = [
        _event(0, "agent_turn.started", {}),
        _event(1, EventType.MODEL_STARTED, {}),
        _event(2, EventType.MODEL_ENDED, {"output": "本次考核小结——15 道选择题，全部完成。"}),
        _event(3, "agent_turn.ended", {}),
    ]
    failures = grade_case14(_fake_solve_result(events))
    assert failures


def test_grade_case14_catches_count_mismatch() -> None:
    # 工具确实被调了，但参数 count 与真实出题数对不上——同样是异常，须被抓住。
    events = [
        _event(
            0, EventType.TOOL_CALL_STARTED, {"tool_name": "start_quiz", "arguments": {"count": 15}}
        ),
        _event(1, LearningEvent.QUESTION_ASKED, {}),
        _event(2, LearningEvent.QUESTION_ASKED, {}),
        _event(3, EventType.TOOL_CALL_ENDED, {"ok": True}),
    ]
    failures = grade_case14(_fake_solve_result(events))
    assert failures


def test_grade_case14_passes_when_tool_call_matches_real_rounds() -> None:
    events = [
        _event(
            0, EventType.TOOL_CALL_STARTED, {"tool_name": "start_quiz", "arguments": {"count": 2}}
        ),
        _event(1, LearningEvent.QUESTION_ASKED, {}),
        _event(2, LearningEvent.QUESTION_ASKED, {}),
        _event(3, EventType.TOOL_CALL_ENDED, {"ok": True}),
    ]
    assert grade_case14(_fake_solve_result(events)) == []


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
