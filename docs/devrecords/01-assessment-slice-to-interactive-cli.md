# 开发日志 · 阶段一：从考核竖切到可交互的持久化学习工具

> 记录区间：M1 事件脊柱 → M2 trace/replay → M3.1–M3.4 考核竖切 → M7 SQLite 持久化 → 交互 CLI。
> 截至本记录：206 测试全绿、pyright strict 0 error、ruff clean；考核循环端到端真机跑通、跨会话持久、终端可交互。
>
> **这份日志是给"面试拷打"准备的**：不只记"做了什么"，而是每处都讲清 **为什么这么做（设计理由）、
> 巧思在哪、以及闭掉/踩过哪些坑**——目的是让你能自然答出"为什么这样设计"，而不是背结论。

---

## 0. 一句话电梯陈述

考核驱动的个人学习工具，工程内核是一个**可观测、可恢复、可评测的 Agent Runtime**：学材料 → 深读抽成
知识点 → 被反复拷问 → 判卷把薄弱概念记进记忆 → 下次薄弱优先复考。工程上最值钱的一句话是：
**"trace、hook、流式输出、eval replay 不是四个模块，是同一条 `AgentEvent` 事件流的四个消费者。"**

---

## 1. 分层与依赖规则（先记这个，面试常问架构）

```
src/grandquiz/
├── kernel/       通用 Agent Runtime：events / clock / runner / db / trace（领域无关）
├── providers/    LLM provider：base(协议) / echo / llm(OpenAI 兼容) / replay(Record·Replay)
├── domain/learning/  学习领域：models / events / store / memory / fetch / reader /
│                     approval / question / grading / routing / selection / assessment / responder / prompts
├── interfaces/cli/   可交互通道：app(argparse) / interactive(Responder) / printer(Rich) / repl
└── evals/        (M8 待建)
```

**分层守卫（一定要能说清方向）**：`kernel/ 禁止 import domain/`；`domain/ 可以 import kernel/`（domain 是
runtime 的消费者）；`interfaces/ 可 import domain+kernel+providers`，但 `domain 禁止 import interfaces`。
> **为什么**：这条依赖方向本身就是卖点——"领域无关的 runtime"。kernel 只认识 `AgentEvent` 信封（`type +
> 元数据 + 不透明 payload`），从不查看 payload 里的领域字段；领域事件（`learning.*`）在 domain 定义、经
> kernel 的 `emit()` 上同一条脊柱。**面试问"你这 runtime 怎么复用到别的领域"→ 答：换掉 domain/learning，
> kernel/providers 一行不动，因为它们从不认识领域类型（M2 有测试 `test_trace_store_persists_unknown_domain_event`
> 用假的 `learning.item_created` 证明 kernel 泛型持久化它不认识的类型）。**

---

## 2. 三个核心设计判断（脱口而出级）

### 2.1 事件脊柱：一条流，四个消费者
`kernel/events.py` 的 `AgentEvent` 是 frozen pydantic 信封：`type / seq / ts / trace_id / span_id /
parent_span_id / payload`。Runner / 编排在每个生命周期节点 `emit()`；`EventSink` 扇出给订阅者。
- trace = 事件的持久化（TraceStore 落库）
- hook = 事件的订阅者（M4）
- 流式/CLI 呈现 = 事件流的网络/终端投影（`QuizEventPrinter` 就是订阅者）
- eval replay = 事件流的回放
> **巧思**：加一种新可观测能力 = 加一个 `sink.subscribe(...)`，而不是改 Runner。**面试角度**：这是
> OpenTelemetry / openai-agents-python 的 tracing-processor 思路的最小手写版，保留完全可控性。

### 2.2 循环是 workflow，不是自由 ReAct（"LLM 判卷，代码记账"）
考核链路是**确定性骨架**：选题→出题→答→判卷→状态转移→记账。LLM **只在"出题""判卷"两个有界槽**被调用；
状态机转移、选题候选集、Learning Memory 写入**全是代码**。
> **为什么**：eval 要可断言、replay 要可对齐——把记账交给 LLM 就没法确定。具体落地：判卷 LLM 只产
> `{verdict, cited_evidence}`，**`weak_item_id` 由代码按 verdict 算**（`assessment.py`），schema 里刻意
> 不含它。**面试拷打"凭什么说可评测"→ 这就是答案**。

### 2.3 确定性三件套（replay 的地基）
1. **时钟注入**（`kernel/clock.py`）：kernel 从不调 `time.time()`，`ts` 来自注入的 `Clock`；`ManualClock`
   每次 `now()` 按 tick 前进（保证 `started < ended`）。
2. **种子化 RNG**：选题的随机走注入的 `new_rng(seed)`，不用全局 `random`。
3. **Record/Replay Provider**（`providers/replay.py`）：录制把 LLM 响应落 cassette，回放直接命中、不触网、
   不烧 token；未命中大声 `ReplayMiss`。
> **面试金句**：**"eval 完全确定、不烧 token，靠三条腿：注入时钟 + 种子化随机 + Record/Replay。replay
> 不是回放旧事件，而是把外部 I/O 从 cassette 喂进同一条确定性管线、让同样的事件重新生成。"**

---

## 3. 逐层实现 · 巧思 · 坑

### M1 — 事件脊柱 + 最小 runner（kernel/events.py, runner.py, clock.py）
- **span = 一对事件**（`*.started` / `*.ended` 共享 `span_id`）；trace 树是事件流的投影，不是落库结构。
- **闭坑① payload 深拷贝隔离**：`AgentEvent` 的 `payload` 在构造时 `copy.deepcopy`（field_validator）。
  > **为什么**：同一事件实例被 sink 扇出给所有订阅者；若发射方发完又改了那个 dict，已落事件会被污染。
  > deepcopy 让事件一经发出就冻结。**面试角度**：多消费者共享不可变事件的经典正确性点。
- **闭坑② 错误也要闭合 span**：runner 里 provider 抛异常时，先发 `MODEL_ENDED(ok=False)` 封口、再发
  `TURN_ENDED`、再 re-raise。否则 TraceStore 会拿到永远开着的 span。**这个"started/ended 配对不变量"后面
  在 reader/grading/assessment 里反复复用**（见 M3.2）。
- **闭坑③ 历史只在成功后提交**：失败的 turn 不把 user 消息留进历史，否则重试会喂给 LLM 两条连续 user。
  测试 `test_failed_turn_leaves_no_orphan_user_message` 钉死。
- **跨轮裁剪**：历史只留最终 assistant 回答（旧仓库 context 膨胀的已知坑，第一天做对）。

### M2 — TraceStore + Record/Replay（kernel/trace.py, db.py; providers/replay.py）
- **trace 存原始事件流，span 树是纯函数投影**：`events` 表 append-only（一行一事件），`build_span_tree`
  是**纯函数**——`*.started` 开 span、`*.ended` 关、`error` 挂到对应 span、按 `parent_span_id` 建森林。
  > **巧思**：事件流是唯一真相，树只是视图；想要别的视图（火焰图/成本汇总）再写个投影函数即可。纯函数 →
  > 脱离 DB 直接手搓事件 list 单测（缝 2）。**面试角度**：这就是对 agent 执行做 event sourcing，trace 是 projection。
- **闭坑④ replay 键必须含 role + model**：`replay_key = sha256(messages) + role + model_id`（用 `\x00`
  分隔拼进 hash 原文）。
  > **为什么**：有 basic=deepseek（判卷）和 enrich=qwen（出题）两个角色。若键只 hash messages，同样的
  > messages 问两个模型会**撞键串答案**，毁掉"完全确定"。**这是"注意力/细节"的绝佳面试证据**——M3.2 的
  > 两个 LLM 槽真的用不同角色，录制时落两条不同 cassette 键，有测试实证不串。
- **闭坑⑤ 迁移不用 alembic**：`PRAGMA user_version` + 顺序 SQL 文件；**迁移文件零时间戳**（否则 replay 对不齐）。
- **闭坑⑥ `Usage.total_tokens` 提成 `computed_field`**（这是 M2 终审我补的坑）：pydantic v2 的
  `model_dump()` **不序列化普通 `@property`**，导致真实 turn 落进 trace 的 usage 没有 total、`Span.tokens`
  对真实数据永远返回 None（只有手搓 payload 才有）。提成 `@computed_field` 后一处定义处处可见。
  > **面试角度**：pydantic v2 序列化语义的坑；也体现"trace schema 优先，token 用量要真能被下游读到"。

### M3.1 — ingest 竖切（fetch / reader / approval / store / ingest）
- **确定性编排 + 注入假件**：`ingest_resource` 是确定性 workflow；fetch 源、provider、审批门全**注入**，
  测试用假件、真机换真实实现，**调用方一行不改**（依赖注入）。
- **闭坑⑦ 领域失败 vs 基础设施失败两分**（这是 M3.1 终审的关键 correctness 修复）：
  - 领域失败（fetch 失败、深读重试用尽）→ 标资源 `failed`、发 `RESOURCE_FETCH_FAILED`、**不 raise**、优雅
    返回（eval case 7：不产幽灵 item）。
  - 基础设施/harness 失败（`ReplayMiss`、provider 传输异常、bug）→ 闭合 span 后**原样冒泡**。
  > **为什么不能一律吞成 failed**：把 `ReplayMiss`（cassette 缺录 = 测试配置错）静默吞成"资源 failed"会
  > **掩盖 eval 配置错误**。**踩坑记录**：workflow 初版把 provider 异常一律归一成 ReaderError，对抗验证
  > 指出这会吞掉 ReplayMiss——改成"只有真·领域失败才优雅降级，基础设施错误闭合 span 后原样抛"。恢复语义
  > （重试/降级）明确留给 M6 RecoveryPolicy，不越界。
- **闭坑⑧ 证据是精确子串**（prompt 调优阶段发现）：Reader 抽的 `cited_evidence` 一开始会去 markdown
  反引号、把多行 bullet 合并成一行，导致引文不能精确匹配原文。加"必须逐字取自原文"的 prompt 约束后，
  单行定义/纠正类证据精确命中（为 ADR-0002 的 locator/出处定位缝铺路：日后回填出处只需 `str.find`）。

### M3.2 — 单题考核（selection / question / grading / responder / assessment）
- **两个 LLM 槽做成工具、不同角色**：出题=enrich(qwen)、判卷=basic(deepseek)，同一注入 provider。
- **两道结构化输出契约门（缝 3）**：
  - 出题门：`cited_evidence` 非空 **且每条逐字命中被考 item 的证据**（防幽灵题，eval case 3）。
  - 判卷门：**对称**——`cited_evidence` 非空且逐字锚定。**踩坑记录**：判卷门初版只查非空、不查真伪，对抗
    验证实测 `cited_evidence=['伪造原文']` 被首调接受——补上与出题门对称的锚定校验（判卷 LLM 不能引伪造原文蒙混）。
- **`ModelRetry` 的 retry_note 用版本无关摘要**（determinism 终审）：pydantic `ValidationError` 的
  `str(exc)` 含**带版本的 URL**（`errors.pydantic.dev/2.13/...`），而 retry_note 会进下一次 prompt、被 hash
  进 replay_key——版本串会让回放随 pydantic 版本漂移。故只取 `loc + type` 稳定摘要。**这个坑很隐蔽，面试
  讲出来很加分**。
- **踩坑记录 · list_type**（真机 prompt 调优时）：真实 deepseek 常把"只有一条"的 `cited_evidence` 返回成
  **裸字符串**而非数组 → pydantic `list_type` 报错、retry 也救不回（模型固执重复同格式）。修法是 **coercion
  （Postel 定律，"输出严格、输入宽容"）**：`CitedEvidence = Annotated[list[str], BeforeValidator(裸串→单元素列表)]`，
  非空/锚定门在其后照常把关。**面试角度**：结构化输出契约要耐得住真机 LLM 的常见偏差，这是 instructor/
  pydantic-ai 都用的模式。

### M3.3 — 薄弱记忆 + 三态状态机（memory / selection / assessment）
- **状态机是纯函数** `apply_verdict(record|None, verdict) -> record|None`（缝 2 命门，逐条 TDD）：
  `错/勉强→薄弱(连对0)`、`薄弱+对→观察中(连对1)`、`观察中+对→销账(从 dict 移除，即连对两次)`、
  `不在记忆+对→不追踪`。**"销账"不是第三个枚举、是从台账移除**。
- **闭坑⑨ ConceptRecord 不变量 validator**（M3.3 终审，M7 才真正兑现价值）：`@model_validator` 强制
  `薄弱↔连对0、观察中↔连对1`。
  > **为什么**：`apply_verdict` 的"对"路径只看 `consecutive_correct` 判销账、**不读 state**。若 M7 的 SQLite
  > 反序列化出脏行（薄弱却 count=1），单次答对就会误销账、跳过观察中。在构造点即拒非法记录，令脏数据
  > **大声失败而非静默错误销账**。**面试金句**：M3.3 埋的这个不变量，到 M7 SQLite 反序列化时正好兜住脏行——
  > "写代码时给未来的接缝留好防御"。
- **踩坑记录 · 假信心测试**（对抗验证用 mutation testing 抓的）：case 6"复考锁定到薄弱 item"的断言其实
  靠 **seed 巧合**通过——把"薄弱优先"选题**禁掉**，测试照样绿。原因：三步用同一 seed，首步 fresh memory
  的全集随机选中的恰好就是后续要断言的 item。修法：照 case 5 的对照法，让薄弱 item **刻意 != 全集随机的
  自然选择**，这样断言才真正区分"薄弱优先"vs"巧合"。**面试角度**：测试给假信心比没测试更危险；对抗式
  mutation 才能揪出来。

### M3.4 — 题型路由 + 追问（routing / question / grading / assessment）
- **路由是纯确定性函数**（按概念在记忆里的状态）：`None(首次)→选择题`、`薄弱→追问`、`观察中→开放`；末尾
  `assert_never(state)` 收口（未来加枚举会在 pyright + 运行期炸出，不静默落"开放"）。
- **巧思 · 选择题确定性判卷**：MC 判卷是**纯代码比对**（所选项文本 == 正确项 → 对/错），**不调 LLM**（PRD
  "选择题确定性比对"）。故 MC 路径**无判卷 model span、无需 cassette、更确定**。
  > **面试角度**：不是所有判卷都要 LLM——能确定化的（MC）就用代码，只有开放/追问才占 LLM 槽。
- **踩坑记录 · MC 选项没校验**（对抗验证抓的 medium）：`options: list[str]` 一开始既非非空、也无去重，
  `['','']` 或 `['A','B','A']` 能过门；而文本比对判卷会被重复/空串选项骗（选空串判"对"）→ **污染薄弱账本**。
  修法：`options: list[NonEmptyStr]` + `_parse_mc` 加去重门。
- **后置追问**：判勉强/错 → 发 `FOLLOWUP_GIVEN` 给正解（确定性组文本，从被考 item 的 summary+evidence）。

### M7 — SQLite 持久化（kernel/db.py; domain store/memory + migrations）
- **巧思 · Protocol + 依赖注入换实现**：抽 `Store`/`Memory` 协议，dict 版（内存/测试）与 SQLite 版都满足；
  `ingest/assess/selection` 的形参类型放宽为协议，**业务逻辑一行没动就能换 SQLite**——兑现骨架台账"替换
  不改调用方"。
- **巧思 · migrate 参数化成通用 runner**：`migrate(conn, migrations_dir=默认 kernel/migrations)`——kernel
  提供通用迁移执行器（更领域无关），domain 拿自己的 `migrations/` + **独立的 learning.db**（与 trace.db 分开，
  各自 `user_version`，不串号）。TraceStore 调用不变（向后兼容）。
- **闭坑⑩ migrate 崩溃安全**（M7 终审）：原来 `user_version` 在循环末尾单次写、**非原子**——若靠后的迁移
  文件失败，前一个 DDL 已提交但版本没跟上，重跑会重放旧文件报"表已存在"。改成**每文件 DDL + 版本号同事务
  提交**（`BEGIN; sql; PRAGMA user_version=N; COMMIT;`），失败停在最后成功编号。
- **踩坑记录 · dict/sqlite 顺序分歧**（对抗验证抓的 medium，两路同时点名）：dict 版 `items_for_task` 返回
  插入序、SQLite 版返回 `ORDER BY item_id`；而 `select_target` 用 `rng.choice` 按下标选——**多资源任务下
  同一 seed 在两实现会选中不同 item**（跨实现行为/replay 不对齐）。修法：两版统一按 `item_id` 升序，定死
  顺序契约。**面试角度**：换持久化实现时，"行为等价"不只是数据对，**顺序**也是契约的一部分。

### 交互 CLI（interfaces/cli: app / interactive / printer）
- **闭坑⑪ Responder 改 async + `.ask_async()`**：考核循环是 async，而 questionary 的 `.ask()` 会起自己的
  事件循环，在已运行的 asyncio loop 里会崩。故 Responder 协议改 `async def answer(...)`、交互实现用
  `.ask_async()`。顺带给 `answer` 加 `options` 参（选择题要把选项传给 responder 渲染成 select）。
- **巧思 · CLI = 事件流消费者**：`QuizEventPrinter` 订阅 `EventSink`，按事件类型渲染（QUESTION_ASKED→Panel、
  ANSWER_JUDGED→着色判决、FOLLOWUP_GIVEN→正解 Panel）——**不另起渲染逻辑，是脊柱的投影**（呼应 2.1）。
- **踩坑记录 · Rich markup 注入崩溃**（对抗验证抓的 **HIGH**，真机必踩）：把动态文本（作答/LLM 题干/**证据
  引文**）未转义拼进 Rich markup 串——真实内容常含 `[...]`（如未闭合的 `[/red`），会让 Rich 抛 `MarkupError`；
  而 **EventSink 不隔离订阅者异常**（那是 M4 HookManager 的职责），异常经脊柱冒泡**炸掉整轮考核**。修法：
  所有动态片段一律 `rich.markup.escape(...)`。**面试金句**：这个 HIGH 体现"信任边界"——LLM 输出和用户输入
  都是不可信文本，进渲染层前必须转义，否则一次真机 quiz 就崩。
- **闭坑⑫ .env 自动加载 + 惰构 provider**：`main()` 启动 `load_dotenv()`（cwd 向上找 .env），让 `grandquiz`
  开箱可用；`quiz` 空库/错任务**先查库、后构造 provider**——无需 LLM key 就能给"先 ingest"指引。

---

## 4. 贯穿全局的巧思清单（速查）

| 巧思 | 一句话 | 在哪 |
|---|---|---|
| 事件脊柱 | 一条 AgentEvent 流，trace/hook/流式/replay 四消费者 | kernel/events |
| span=事件对、树是投影 | build_span_tree 纯函数、事件流是唯一真相 | kernel/trace |
| replay 键含 role+model | 防 deepseek/qwen 同 messages 撞键 | providers/replay |
| 确定性 ID | derive_id = sha256 + NUL 分隔 + **NFC 归一化**，禁 uuid4 | domain/models |
| 无时间戳 | 时序来自事件流 seq/ts，模型/表都不存 created_at | 全域 |
| computed_field | total_tokens 要序列化进 trace | providers/base |
| 内容 hash 版 prompt | 改 prompt 自动换版本、旧 cassette 自动失效强制重录 | domain/prompts |
| coercion | 裸串→列表（Postel 定律），耐真机 LLM 偏差 | domain/models |
| 确定性 MC 判卷 | 选择题代码比对、不占 LLM 槽、无需 cassette | domain/grading |
| 不变量 validator | state↔count 一致，兜 SQLite 脏行 | domain/memory |
| Protocol + DI | dict↔sqlite 换实现不改调用方 | domain/store,memory |
| 每文件原子迁移 | DDL+版本号同事务，崩溃安全 | kernel/db |
| assert_never | 枚举穷尽，未来加值会炸出 | domain/routing |
| escape 动态文本 | 防 markup 注入炸渲染 | interfaces/cli/printer |

## 5. 走骨架 + 台账（工程节奏，面试可讲"怎么控节奏"）
"竖切先穿透"：先用最小假件（dict 假 memory/store、脚本化审批/作答）让整条链路亮起来，kernel 各层由真实
domain 拉动着逐层加硬。**每处临时假件打 `# SKELETON(Mx):` 标记 + 记入 `docs/skeleton-ledger.md`**，
`grep -rn SKELETON src/` 数应 = 台账未完成行数——防"跑通即遗忘"。M7 把 #1/#2（dict store/memory）销账成
SQLite，标记 5→3（余 approval CLI 交互形态 / reader 内联执行器 / responder 已落交互但 suspend-resume 待做）。

## 6. 测试哲学（三条缝）
- **缝 1（事件/trace 流，主缝）**：用假/回放 provider 驱动跑脚本化输入，断言发射的 AgentEvent 流（含领域
  事件）。8 个 eval 用例都活在这条缝。
- **缝 2（确定性核心单元）**：纯函数直接 TDD——状态机 `apply_verdict`、`build_span_tree`、`derive_id`、
  MC 判卷、路由。这些是 eval 命门不变量。
- **缝 3（结构化输出契约）**：喂畸形/未锚定/伪造响应，断言 validator 拒绝并触发 ModelRetry。
- **LLM 的两个槽不 unit-TDD**：靠 Record/Replay 录放 + golden cassette 回归（真机录一次、CI 零 token 重放）。

## 7. 我犯过的过程坑（诚实记录，面试也能当"工程成熟度"讲）
1. **推了一个 lint 失败的 commit**：命令里 `ruff check` 和 `git commit` 是分开的语句（非一个 `&&` 链），
   ruff 挂了但 commit 照跑、还 push 了 → 远端 CI 会红。修法：把整条门做成严格 `&&` 链，**全绿才 commit+push**。
2. **workflow 脚本解析崩**：给 build agent 的说明里用了 RST 风格 `` ``code`` `` 行内标记，反引号和 JS 模板
   字符串定界符冲突、提前截断。修法：说明文字改用字符串数组 `join('\n')`、零裸反引号。
3. **café Unicode 匹配失败**：Edit 工具匹配含重音字符的行时 NFC/NFD 归一化歧义，反复匹配不上。修法：改用
   `chr(0x00E9)` 显式码点构造，源文件纯 ASCII 无歧义。（这也间接催生了 derive_id 的 NFC 归一化——中文用户
   多来源文本同理。）

## 8. 面试拷打 Q&A（预演）
- **Q：你这 runtime 凭什么叫"领域无关"？** A：kernel 只认识 AgentEvent 信封，从不看 payload 里的领域字段；
  领域事件是 `learning.*` 字符串常量、payload=model_dump()，经 kernel emit() 上脊柱；有测试用假的领域事件
  类型证明 kernel 泛型持久化它不认识的类型。换 domain，kernel/providers 不动。
- **Q：怎么保证 eval 可复现、不烧钱？** A：确定性三件套（注入时钟 + 种子 RNG + Record/Replay），replay 键
  含 role+model 防串键；golden cassette 真机录一次、CI 零 token 逐字节重放。
- **Q：LLM 输出不可靠，你怎么防？** A：三层——结构化输出 schema + ModelRetry 有界重试（缝 3）；证据必须
  逐字锚定真实原文（防幽灵题/幽灵引文）；coercion 兜真机常见偏差（裸串→列表）。且"LLM 判卷、代码记账"，
  记账不交给 LLM。
- **Q：状态机的销账逻辑，脏数据会不会出错？** A：apply_verdict 是纯函数、逐条 TDD；ConceptRecord 有
  state↔count 不变量 validator，SQLite 反序列化脏行在构造点就炸，不会静默错误销账。
- **Q：换 SQLite 持久化时最容易出的错？** A：不只是数据对，**顺序契约**也是行为的一部分——dict 插入序 vs
  SQLite ORDER BY 不一致会让种子化选题跨实现漂移；统一按 item_id 排序。还有迁移的**原子性**（DDL+版本号同事务）。
- **Q：为什么 CLI 不另写渲染，而是订阅事件？** A：CLI 是事件脊柱的投影（第四个消费者）；`print→Rich→未来
  Textual` 是纯展示层替换，碰不到 runtime。附带的坑：动态文本要 escape，否则 markup 注入炸渲染。

## 9. 下一步
- **M8 Eval Harness**（下一个）：把 8 个 eval 用例升成 inspect_ai 式 Task/Solver/Scorer + 报告，用 golden
  cassette 零 token 跑回归——把"可评测的 Agent Runtime"做实。
- M4 HookManager（异常隔离——正好解决第 8 节里"EventSink 不隔离订阅者异常"）、M5 ContextBuilder、
  M6 RecoveryPolicy、审批/作答的 suspend-resume 正式化（台账 #3/#6）。
