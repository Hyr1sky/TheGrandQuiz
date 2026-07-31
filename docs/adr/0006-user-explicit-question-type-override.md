# ADR-0006: 用户显式题型覆盖——对 routing.py 契约"题型由代码定"的例外

- 状态：已接受
- 日期：2026-07-09

## 背景

第五轮真机 dogfood 抓到 **#1 错题型**：用户要"代理通信协议的**简答题**"，系统却出了**选择题**。
根因是 `start_quiz` 没有"要哪种题型"的旋钮——题型完全由
`route_question_type(state)` 按被考概念在 Learning Memory 里的状态自适应决定（首次接触 → 选择题、
薄弱 → 追问、观察中 → 开放）。用户新学一份材料时概念全是"首次接触"，于是无论用户怎么点都只能拿到
选择题。

`routing.py` 的书面契约是 ADR-0004（"LLM 判卷，代码记账"）的姊妹面：**题型也由代码定，不由 LLM
挑**。这个契约本身是对的（题型是确定性决策、可 replay、可 eval 断言），但它没有给"用户显式指定"
留任何入口。用户心智里"我说了算"——我点了简答题，就该拿到简答题，不该被记忆状态的自适应路由盖过。

难点在于：既要让用户的显式意图胜出，又不能把题型决策交回给 LLM 自由发挥（那会破坏 replay 地基、
也正是 ADR-0004 刻意排除的自由 ReAct）。而且 LLM 恰恰在这件事上不可靠——dogfood 里它就把"简答"
误导向了选择题。

## 决策

开一道**受控例外**：`start_quiz` 增一个可选 `question_type` 意图字段，用户显式指定的题型**胜过**
`route_question_type` 的记忆状态自适应路由；不指定时仍按薄弱状态自适应路由（缺省契约不变）。

机制严守 ADR-0004——**LLM 只抽用户意图短语，代码用冻结同义表把短语映射到既有三题型**：

- `routing.py` 落一张**冻结同义映射表** `_QUESTION_TYPE_INTENTS`（意图短语 → 既有三题型枚举）：
  "简答"/"简答题"/"short answer"/"问答" → **开放**；"选择"/"选择题"/"multiple choice" → 选择题；
  "追问"/"深挖"/"probe" → 追问。查表前把短语 `strip().casefold()` 归一。
- 纯函数 `resolve_question_type(intent, state)` 三分支：`intent is None` → `route_question_type(state)`
  （自适应，字节不变）；`intent` 命中映射表 → 映射结果（胜过自适应）；`intent` 未知 →
  `route_question_type(state)` 回落（fail-soft，不硬报错）。
- **护栏**：短答类意图（"简答"等）**绝不**产出"选择题"。映射表按此构造，并在模块 import 期用断言
  钉死（`_SHORT_ANSWER_INTENTS` 里任一短语映射到"选择题"即在构造期炸出）——从代码层杜绝 LLM 把
  "简答"误导向选择题、静默复现 #1。
- **不新增第 4 题型**（YAGNI）：复用既有三题型枚举，故**不新增 prompt、不新增 grading 路径**
  （选择题仍确定性判卷、开放 / 追问仍 LLM + cassette）；"简答"作为独立第一类题型的可能性留二期
  （`assert_never` 缝）。
- `assess_once` 增 `question_type: str | None = None`，路由处 `effective = resolve_question_type(
  question_type, state)`；`QUESTION_ASKED` payload 同记 `routed`（自适应会给的）与 `effective`
  （实际用的），供 eval 断言用户意图是否透传；`AssessmentResult.question_type` 透出 `effective`。
- `start_quiz` 的 `_StartQuizParams` 增 `question_type`；工具 description 教 LLM 只抽用户原话里的
  题型意图短语原样填入，别自造题型、别把"简答"填成"选择题"。

`question_type=None`（缺省）逐字节等价于改动前：`effective == routed`，默认路径的 message /
replay_key / prompt 版本号 / golden cassette 一字不变。

## 备选方案

- **让 LLM 直接产出题型枚举**：被 ADR-0004 排除——题型是确定性决策，交给 LLM 会破坏 replay、且
  dogfood 已证明 LLM 在这上面不可靠（把"简答"路由成选择题）。
- **代码做模糊 / 分词匹配用户原话**：会引入大小写 / 中文规范化的 parity 陷阱（与 GKB scope 分叉
  刻意规避的同一坑），也让 replay 不稳。冻结精确同义表 + 归一是确定性的、可 mutation-kill。
- **新增"简答"为第一类题型**：需新 prompt + 新 grading 路径 + 重录 cassette，超出修 #1 所需
  （YAGNI）。既有"开放"已是简答式问答，复用即可；真要区分再走二期。
- **护栏只靠映射表构造、不加断言**：一次手滑编辑就能把"简答"指向"选择题"静默复现 #1。构造期断言
  把这个不变量变成 import 就炸的硬约束。

## 后果

- **好处**：用户"我说了算"的心智被满足（修 #1 错题型），且没有牺牲 replay / eval 地基——题型仍是
  确定性代码决策，LLM 只贡献一个被录进 completion 之外的意图短语。护栏从代码层根除"简答→选择题"
  这类静默 bug。缺省路径零行为变化，加法式演进。
- **代价 / 风险**：冻结同义表是**封闭词汇**——用户说了表里没有的题型说法（如"填空题"）会静默回落
  自适应路由，而非报错。这是刻意的 fail-soft（宁可回落也不炸考核），但意味着新增说法要手动扩表。
  未来若要开放式题型或按材料定制题型，需重新审视这张表 + 是否升级为第一类题型（`assert_never` 缝）。
- **重新审视信号**：出现"用户反复点某个表外说法却总拿到自适应题型"的 dogfood 反馈；或"简答"需要
  与"开放"区分（独立 prompt / grading）时——那就该把它提升为第 4 题型并重录 cassette。

## 2026-07-31 修订：多题请求必须先形成 AssessmentPlan

真实 Web trace 暴露了新的同类问题：用户要求“两道选择、一道简答”，Chat adapter 却只把
`rounds=3` 传给 FastAPI，后端于是按同一个题型/自适应规则跑三轮。CLI 已经有分段展开，但 Web
复制的是更早的单题型接口；OpenAPI 只能证明 HTTP 字段合法，不能证明两个 adapter 解释了同一种语义。

因此本 ADR 的“显式意图胜过自适应”从单题扩展到批次：

- `AssessmentPlan` 是唯一的批次规范化接口，输出逐位置 `question_type_intents`；
- CLI `start_quiz`、Web Chat `start_assessment` 和 FastAPI Assessment 都消费这份有序计划；
- Web 导航事件与 HTTP 请求传 `question_type_plan`，不得再把混合序列压扁成
  `rounds + question_type`；
- 旧 HTTP `rounds/question_type` 暂留兼容入口，但进入 manager 后立即规范化为计划，workflow 内部
  不保留第二套表示；
- Pydantic/OpenAPI 负责单 adapter 的形状，跨 adapter conformance tests 负责语义等价。

这不是新增题型裁决：每个位置仍复用 `resolve_question_type` 与本 ADR 的冻结同义表。变化只是把
“用户明确说了什么”在进入异步 workflow 前完整保存下来。
