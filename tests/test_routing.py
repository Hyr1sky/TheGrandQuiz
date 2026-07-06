"""题型路由测试（缝 2）——纯函数三分支逐条钉死，无 I/O、无 provider。

路由是 eval case 8 的命门不变量：按被考概念在 Learning Memory 的状态选题型。三条规则各钉一条：
None（首次接触 / 未追踪）→ 选择题、薄弱（复考仍挣扎）→ 追问、观察中（在改善）→ 开放。
"""

import pytest

from grandquiz.domain.learning.memory import ConceptState
from grandquiz.domain.learning.routing import QuestionType, route_question_type


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
