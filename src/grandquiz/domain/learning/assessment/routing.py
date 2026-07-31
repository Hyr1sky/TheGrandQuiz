"""题型路由——按被考概念在 Learning Memory 里的状态确定性地选题型（缝 2，纯函数、无 I/O）。

ADR-0004（"LLM 判卷，代码记账"）的姊妹面：**题型也由代码定，不由 LLM 挑**。路由读被考 item
在 Learning Memory 的 ``ConceptState``（``None`` = 首次接触 / 未追踪），按下表定题型；决策上脊柱
（``QUESTION_ASKED`` 的 payload 带 ``question_type``），故可在 trace / 事件流断言（eval case 8）。

三条路由规则——让拷问有层次，难度随掌握程度走：

- ``None``（首次接触 / 未追踪）→ **选择题**：低门槛热身，先建立接触（此路径判卷走确定性代码）。
- ``薄弱``（复考、仍挣扎）→ **追问**：在标准开放题之上再逼一层，把盲区深挖出来。
- ``观察中``（在改善）→ **开放**：标准开放问答，确认是否真的稳住了。

**用户显式题型覆盖（ADR-0006 的例外）**：上面的自适应路由是缺省契约"题型由代码定"。GKB-S5 开一道
受控例外——用户可显式点题型（"出简答题"），这一指定**胜过**记忆状态自适应路由。机制仍严守
ADR-0004：**LLM 只抽用户意图短语，代码用下方冻结同义表把短语映射到既有三题型**（不新增第 4 题型），
未知 / 缺省回落 ``route_question_type`` 自动路由。护栏：**短答类意图代码层禁止映射到"选择题"**
（防 LLM 把"简答"误导向选择题、静默复现 dogfood #1"要简答却出选择题"）。入口是纯函数
``resolve_question_type(intent, state)``；机制之为何见 ADR-0006。
"""

from typing import Literal, assert_never

from grandquiz.domain.learning.memory import ConceptState

# 三种题型：选择题（确定性判卷）/ 开放（LLM 判卷）/ 追问（LLM 判卷，深挖 prompt 变体）。
QuestionType = Literal["选择题", "开放", "追问"]

# 冻结同义映射（ADR-0006）：用户意图短语 → 既有三题型。查表前把短语 ``strip().casefold()`` 归一
# （首尾空白 / 英文大小写无关；中文 casefold 恒等），故键一律以归一形式登记。**不新增第 4 题型**
# （YAGNI；``assert_never`` 缝留二期）。短答类意图（"简答"等）刻意只指向"开放"，绝不指向"选择题"。
_QUESTION_TYPE_INTENTS: dict[str, QuestionType] = {
    # 短答 / 问答意图 → 开放（标准开放问答；短答不是第 4 题型，复用"开放"）。
    "简答": "开放",
    "简答题": "开放",
    "short answer": "开放",
    "问答": "开放",
    "开放": "开放",
    "开放题": "开放",
    # 选择意图 → 选择题。
    "选择": "选择题",
    "选择题": "选择题",
    "multiple choice": "选择题",
    # 追问 / 深挖意图 → 追问。
    "追问": "追问",
    "深挖": "追问",
    "probe": "追问",
}

# 短答类意图集（归一形式）——护栏的锚：这些短语**绝不**允许映射到"选择题"。
_SHORT_ANSWER_INTENTS: frozenset[str] = frozenset({"简答", "简答题", "short answer", "问答"})

# 构造期护栏（ADR-0006）：钉死"短答意图 ↛ 选择题"。任何后续编辑把某个短答短语指向"选择题"都会在
# import 期在此炸出，而非静默把"简答"路由成选择题、复现 dogfood #1。
assert all(_QUESTION_TYPE_INTENTS[phrase] != "选择题" for phrase in _SHORT_ANSWER_INTENTS), (
    "短答类意图禁止映射到选择题（ADR-0006 护栏）"
)


def route_question_type(state: ConceptState | None) -> QuestionType:
    """按被考概念状态定题型（纯函数，无 I/O、不发事件）。见模块 docstring 的三条规则。"""
    if state is None:
        return "选择题"
    if state == "薄弱":
        return "追问"
    if state == "观察中":
        return "开放"
    assert_never(state)  # 穷尽 ConceptState；未来加枚举会在此炸出，而非静默落进"开放"


def resolve_question_type(intent: str | None, state: ConceptState | None) -> QuestionType:
    """把**用户显式题型意图短语**解析为有效题型；无意图 / 未知则回落自适应路由（纯函数，ADR-0006）。

    三分支（缺省与旧行为字节等价）：

    - ``intent is None``（用户没点题型）→ ``route_question_type(state)``：现行记忆状态自适应路由，
      **字节不变**（既有默认路径 message / replay_key / cassette 一字不动）。
    - ``intent`` 命中冻结同义表 → 映射结果（**胜过**自适应路由，"我说了算"）。
    - ``intent`` 未知（表里没有的短语）→ ``route_question_type(state)`` 回落（fail-soft，不硬报错——
      LLM 抽了个没登记的短语时诚实退回自适应，而非炸掉整轮考核）。

    护栏：短答类意图（"简答"等）经冻结表**只会**得到"开放"，代码层杜绝其产出"选择题"（映射表按此
    构造 + 模块级构造期断言钉死）——防 LLM 把"简答"误导向选择题、静默复现 dogfood #1。
    """
    if intent is None:
        return route_question_type(state)
    mapped = _QUESTION_TYPE_INTENTS.get(intent.strip().casefold())
    if mapped is None:
        return route_question_type(state)  # 未知短语 → 回落自适应路由（fail-soft）
    return mapped


def is_supported_question_type_intent(intent: str | None) -> bool:
    """Whether an input is a recognized explicit override rather than fail-soft text."""

    return intent is not None and _QUESTION_TYPE_INTENTS.get(intent.strip().casefold()) is not None
