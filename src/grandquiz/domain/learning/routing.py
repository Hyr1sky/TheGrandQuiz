"""题型路由——按被考概念在 Learning Memory 里的状态确定性地选题型（缝 2，纯函数、无 I/O）。

ADR-0004（"LLM 判卷，代码记账"）的姊妹面：**题型也由代码定，不由 LLM 挑**。路由读被考 item
在 Learning Memory 的 ``ConceptState``（``None`` = 首次接触 / 未追踪），按下表定题型；决策上脊柱
（``QUESTION_ASKED`` 的 payload 带 ``question_type``），故可在 trace / 事件流断言（eval case 8）。

三条路由规则——让拷问有层次，难度随掌握程度走：

- ``None``（首次接触 / 未追踪）→ **选择题**：低门槛热身，先建立接触（此路径判卷走确定性代码）。
- ``薄弱``（复考、仍挣扎）→ **追问**：在标准开放题之上再逼一层，把盲区深挖出来。
- ``观察中``（在改善）→ **开放**：标准开放问答，确认是否真的稳住了。
"""

from typing import Literal, assert_never

from grandquiz.domain.learning.memory import ConceptState

# 三种题型：选择题（确定性判卷）/ 开放（LLM 判卷）/ 追问（LLM 判卷，深挖 prompt 变体）。
QuestionType = Literal["选择题", "开放", "追问"]


def route_question_type(state: ConceptState | None) -> QuestionType:
    """按被考概念状态定题型（纯函数，无 I/O、不发事件）。见模块 docstring 的三条规则。"""
    if state is None:
        return "选择题"
    if state == "薄弱":
        return "追问"
    if state == "观察中":
        return "开放"
    assert_never(state)  # 穷尽 ConceptState；未来加枚举会在此炸出，而非静默落进"开放"
