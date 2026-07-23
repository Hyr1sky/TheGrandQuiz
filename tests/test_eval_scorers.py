"""缝-2：两个质量回归 scorer 的直接单测——喂合成 QUESTION_ASKED 事件流，不跑 harness。

scorer 是纯规则函数（读事件流 → 失败明细列表），故可脱离 Solver 直测：这里手工造 ``QUESTION_ASKED``
事件（混语言 / 含重复 / 干净）验证 scorer 在该报错时报错、该放行时放行——是 case9 / case10 端到端绿
之外更细粒度的回归锚点（scorer 逻辑一旦退化，这里先红）。
"""

from typing import Any

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.case import AssessCase
from grandquiz.evals.graders.scorers import (
    cjk_ratio,
    language_bucket,
    language_consistency,
    no_duplicate,
)
from grandquiz.evals.harness import SolveResult
from grandquiz.kernel.events import AgentEvent


def _asked(question: str, options: list[str] | None = None, *, seq: int) -> AgentEvent:
    payload: dict[str, Any] = {"question": question}
    if options is not None:
        payload["options"] = options
    return AgentEvent(
        type=LearningEvent.QUESTION_ASKED,
        seq=seq,
        ts=float(seq),
        trace_id="t",
        payload=payload,
    )


def _sr(events: list[AgentEvent]) -> SolveResult:
    # 造一个最小 SolveResult：scorer 只读 sr.events，其余字段填占位（不被 scorer 触碰）。
    case = AssessCase(id="synthetic", expected_events=[])
    return SolveResult(
        case=case,
        events=events,
        spans=[],
        result=None,
        store=LearningStore(),
        memory=LearningMemory(),
        calls=0,
        roles=[],
        context={},
    )


# --- CJK 分桶辅助 ---------------------------------------------------------------------


def test_cjk_ratio_and_bucket() -> None:
    assert cjk_ratio("闭包是什么") == 1.0
    assert cjk_ratio("what is a closure") == 0.0
    assert language_bucket("闭包捕获的是变量还是值？") == "zh"
    assert language_bucket("Does a closure capture the variable?") == "en"
    # 半汉半英 → mixed 桶（既非 zh 也非 en）。
    assert language_bucket("closure 是 variable 还是 value") == "mixed"


# --- language_consistency -------------------------------------------------------------


def test_language_consistency_passes_on_uniform_chinese() -> None:
    # 全中文同桶（含中文选项）→ 期望 zh → 无失败。
    events = [
        _asked("闭包捕获的是变量还是值？", ["变量本身", "值的快照"], seq=0),
        _asked("闭包如何延长变量生命周期？", ["延长作用域", "复制值"], seq=1),
    ]
    assert language_consistency(_sr(events), "zh") == []


def test_language_consistency_flags_mixed_language_session() -> None:
    # 混语言流：第一题中文、第二题英文 → 期望 zh → 英文题落 en 桶 → 报错（且全会话出现多桶）。
    events = [
        _asked("闭包捕获的是变量还是值？", seq=0),
        _asked("Does a closure capture the variable or its value?", seq=1),
    ]
    failures = language_consistency(_sr(events), "zh")
    assert failures  # 非空 = 报错
    assert any("多桶" in f or "!= 期望" in f for f in failures)


def test_language_consistency_flags_option_language_drift() -> None:
    # 题干中文但选项漂成英文 → 期望 zh → 选项落 en 桶 → 报错（AC 要求 question / options 都判）。
    events = [_asked("闭包捕获的是变量还是值？", ["the variable itself", "a snapshot"], seq=0)]
    assert language_consistency(_sr(events), "zh")


def test_language_consistency_empty_stream_is_a_failure() -> None:
    # 无 QUESTION_ASKED → 无法判语言一致性 → 报错（不静默通过）。
    assert language_consistency(_sr([]), "zh")


# --- no_duplicate ---------------------------------------------------------------------


def test_no_duplicate_passes_on_distinct_questions() -> None:
    events = [
        _asked("什么是闭包？", seq=0),
        _asked("闭包如何捕获它引用的变量？", seq=1),
    ]
    assert no_duplicate(_sr(events)) == []


def test_no_duplicate_flags_verbatim_repeat_ignoring_punctuation_and_case() -> None:
    # 归一化后相等（半角问号 / 多余空白）→ 视为逐字重复 → 报错。
    events = [
        _asked("什么是闭包？", seq=0),
        _asked("  什么是闭包 ? ", seq=1),
    ]
    failures = no_duplicate(_sr(events))
    assert failures
    assert any("逐字重复" in f for f in failures)
