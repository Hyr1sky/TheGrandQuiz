"""题型路由测试（缝 2）——纯函数三分支逐条钉死，无 I/O、无 provider。

路由是 eval case 8 的命门不变量：按被考概念在 Learning Memory 的状态选题型。三条规则各钉一条：
None（首次接触 / 未追踪）→ 选择题、薄弱（复考仍挣扎）→ 追问、观察中（在改善）→ 开放。

GKB-S5（缝 3，ADR-0006）：`resolve_question_type(intent, state)` 冻结同义映射 + 用户显式题型覆盖。
逐条钉死——None→自适应（字节等价 route_question_type）、命中映射→胜过自适应、未知→回落自适应、
**短答意图 ↛ 选择题护栏**（防复现 dogfood #1"要简答却出选择题"）。
"""

import pytest

from grandquiz.domain.learning.memory import ConceptState
from grandquiz.domain.learning.routing import (
    QuestionType,
    resolve_question_type,
    route_question_type,
)

# 护栏锚（与 routing._SHORT_ANSWER_INTENTS 同一集，此处显式列出以断言外部行为、不引私有符号）：
# 这些短答类意图短语**绝不**得到"选择题"。
_SHORT_ANSWER_PHRASES = ["简答", "简答题", "short answer", "问答"]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, "选择题"),  # 首次接触 / 未追踪 → 热身选择题
        ("薄弱", "追问"),  # 复考仍挣扎 → 深挖追问
        ("观察中", "开放"),  # 在改善 → 标准开放确认
    ],
)
def test_route_question_type(state: ConceptState | None, expected: QuestionType) -> None:
    assert route_question_type(state) == expected


# --------------------------------------------------------------------------- #
# GKB-S5：resolve_question_type——冻结同义映射 + 用户显式覆盖（ADR-0006）
# --------------------------------------------------------------------------- #

_ALL_STATES: list[ConceptState | None] = [None, "薄弱", "观察中"]


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        # 短答 / 问答意图 → 开放（复用既有题型，不新增第 4 型）。
        ("简答", "开放"),
        ("简答题", "开放"),
        ("short answer", "开放"),
        ("问答", "开放"),
        ("开放", "开放"),
        ("开放题", "开放"),
        # 选择意图 → 选择题。
        ("选择", "选择题"),
        ("选择题", "选择题"),
        ("multiple choice", "选择题"),
        # 追问 / 深挖意图 → 追问。
        ("追问", "追问"),
        ("深挖", "追问"),
        ("probe", "追问"),
    ],
)
@pytest.mark.parametrize("state", _ALL_STATES)
def test_explicit_intent_maps_regardless_of_state(
    intent: str, expected: QuestionType, state: ConceptState | None
) -> None:
    # 命中冻结映射 → 映射结果，**胜过**记忆状态自适应路由（对所有 state 恒成立，与 state 无关）。
    assert resolve_question_type(intent, state) == expected


@pytest.mark.parametrize("state", _ALL_STATES)
def test_none_intent_falls_back_to_adaptive_routing(state: ConceptState | None) -> None:
    # intent is None → 字节等价 route_question_type（默认路径不变，既有 eval / cassette 一字不动）。
    assert resolve_question_type(None, state) == route_question_type(state)


@pytest.mark.parametrize("state", _ALL_STATES)
@pytest.mark.parametrize("intent", ["填空题", "口试", "", "随便什么没登记的短语"])
def test_unknown_intent_falls_back_to_adaptive_routing(
    intent: str, state: ConceptState | None
) -> None:
    # 未知短语 → 回落自适应（fail-soft，不硬报错、不炸考核）。
    assert resolve_question_type(intent, state) == route_question_type(state)


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("  简答  ", "开放"),  # 首尾空白无关（strip）
        ("Short Answer", "开放"),  # 英文大小写无关（casefold）
        ("SHORT ANSWER", "开放"),
        ("Multiple Choice", "选择题"),
        ("Probe", "追问"),
    ],
)
def test_intent_normalized_strip_and_casefold(intent: str, expected: QuestionType) -> None:
    # 查表前 strip().casefold() 归一——大小写 / 首尾空白无关（确定性，无模糊子串匹配）。
    assert resolve_question_type(intent, None) == expected


@pytest.mark.parametrize("state", _ALL_STATES)
def test_short_answer_intent_never_maps_to_multiple_choice(state: ConceptState | None) -> None:
    # 护栏（ADR-0006，修 #1）：任一短答类意图，在任一记忆状态下，**绝不**产出"选择题"。
    # 去掉映射表护栏 / 把"简答"错指向"选择题"→ 本断言被杀。
    for intent in _SHORT_ANSWER_PHRASES:
        assert resolve_question_type(intent, state) != "选择题"
        assert resolve_question_type(intent, state) == "开放"
