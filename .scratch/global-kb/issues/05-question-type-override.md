# GKB-S5 — 题型冻结映射 + `question_type` 用户覆盖（修 #1 错题型）

Status: ready-for-agent
Type: AFK（question_type-honor eval 用例属 GKB-S7；本 slice 走单元 + 事件断言）

## Parent
[PRD: 全局 KB 重构](../PRD.md)

## What to build

让用户能显式指定题型（"出简答题"），且这一指定**胜过** `route_question_type` 的记忆状态自适应路由。机制严守
ADR-0004：**LLM 只抽用户意图短语，代码用冻结同义表把短语映射到既有三题型**，短答意图**代码层禁止映射到选择题**，
未知/缺省回落自动路由。修 **#1 错题型**（要简答却出选择题）。

## 锁定设计（不留给实现猜）

- **冻结同义映射（落 `routing.py`）**：常量映射表 `intent 短语 → QuestionType`（既有三型：选择题/开放/追问）。
  例："简答"/"简答题"/"short answer"/"问答" → **开放**；"选择"/"选择题"/"multiple choice" → 选择题；
  "追问"/"深挖"/"probe" → 追问。**不新增第 4 题型**（YAGNI；`assert_never` 缝留二期）。
- **`resolve_question_type(intent, state)`**（纯函数）：
  - `intent is None` → `route_question_type(state)`（现行自适应，字节不变）。
  - `intent` 命中映射表 → 映射结果。
  - `intent` 未知 → 回落 `route_question_type(state)`（fail-soft，不硬报错）。
  - **护栏**：短答类意图**绝不**产出"选择题"（映射表按此构造 + 显式断言；防 LLM 把"简答"误导向选择题、静默复现 #1）。
- **`assess_once` 接 `question_type: str | None = None`**（意图短语）：路由处
  `effective = resolve_question_type(question_type, memory.state_of(target))`。复用既有三型 →
  **不新增 prompt / 不新增 grading 路径**（MC 仍确定性判卷、开放/追问仍 LLM+cassette）。
- **事件**：`QUESTION_ASKED` payload **同记 `routed`（自动路由会给的）与 `effective`（实际用的）**，供 eval 断言意图透传。
  `AssessmentResult.question_type` 透出的是 effective。
- **`start_quiz`**：`_StartQuizParams` 加 `question_type: str | None = None`；工具 description 教 LLM 只抽用户意图短语。
- **ADR + docstring**：出 ADR「用户显式题型覆盖对 routing.py 契约'题型由代码定'的例外」；同步 `routing.py` docstring。
- **确定性**：映射是冻结纯代码；`question_type=None` 默认 → 现有单测/eval/cassette 字节不变。

## Acceptance criteria

- [ ] `routing.py` 冻结同义映射 + `resolve_question_type(intent, state)`：None→自动、命中→映射、未知→回落
- [ ] **护栏断言**：短答类意图绝不 → 选择题
- [ ] `assess_once` 接 `question_type`（默认 None）；`start_quiz` 接 `question_type` 并透传；description 教抽意图
- [ ] `QUESTION_ASKED` 同记 routed + effective；`AssessmentResult.question_type` = effective
- [ ] **question_type-honor 行为**：`question_type="简答"` → effective=开放（事件断言不出选择题）
- [ ] ADR 落 `docs/adr/`；`routing.py` docstring 记例外
- [ ] TDD：映射×各短语、未知回落、None 自动、短答↛选择题护栏，各 mutation 可杀
- [ ] `question_type=None` 默认路径：既有 8+2 eval + cassette 字节不变（不新增第 4 型/新 prompt）
- [ ] 五门全绿（含 lint-imports）

## Files (owner, 可能漂)
`domain/learning/routing.py`(映射 + resolve + docstring)、`domain/learning/assessment.py`(override + effective)、
`domain/learning/tools.py`(_StartQuizParams + description)、`docs/adr/000X-*.md`、
`tests/test_routing.py`、`tests/test_assessment.py`。

## Blocked by
GKB-S2（assess_once 签名重塑后再加参数）。可与 GKB-S3 / GKB-S4 并行。
