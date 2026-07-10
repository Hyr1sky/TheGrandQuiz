# PRD：全局 KB 重构（Global Knowledge Base）——会话从"绑一个 task 标题"解耦成"操作持久全局知识库"

Status: done（S1-S7 全 merge，main `bb51d5a`，五门全绿 501 passed、eval 13/13；#1+#2 已修，2026-07-10 用户真机 dogfood 通过。ADR-0005/0006 落地。dogfood 抓到一个非阻断项：混合题型请求"一道选择一道简答"时 LLM 未拆成两次 typed 调用——属工具描述/编排债，见收尾讨论。）
Triage: ready-for-agent

## Problem Statement

真机第五轮 dogfood 抓到两个同根问题：

- **#1 考错知识库 + 错题型**：用户要"代理通信协议的**简答题**"，系统却出了 **Hook 库的选择题**。`start_quiz`
  没有"考哪个主题 / 材料"和"要哪种题型"的旋钮——只能在当前 task 的全量池里按 memory 状态自动路由题型、随机选题。
- **#2 换标题就"丢"知识**：两次会话用了不同启动标题（`react "Hook 详解"` vs `react "代理通信协议"`），上一次
  ingest 的知识在这次"消失"了。根因不是持久化 bug，而是**范围模型**：知识按 `task_id = derive_id(title)` 分库，
  换标题即换库。

用户心智里这是**一个持久的个人学习库**，想用自然语言在里面切换不同材料问答（"考代理通信协议""换 Hook 的考我"），
而不是"一个启动标题锁死一份文件"。当前"会话 = 一个 task 标题"的绑定，把持久全局库切成了互不相通的孤岛。

## Solution

把 react/quiz 会话从"绑死一个 task 标题"**解耦成"操作持久全局知识库"**：

- 所有 ingest 的知识**不分标题进同一个池**（`resource_id = derive_id(url)`，内容寻址、同 URL 全局唯一），
  消灭"换标题换库"——修 **#2**。
- `start_quiz` 增两个可选旋钮：**scope（考哪些材料）** 与 **question_type（要哪种题型）**——修 **#1**。
- scope 走**目录式**：把全库 `{resource_id → topic}` 清单注入 ReAct 上下文，LLM 从清单里认出用户意图对应哪个
  资源、填入 **exact resource_id**，代码按 id 精确过滤。**语义匹配交给 LLM（其强项，天然会 ACP↔代理通信协议），
  确定性过滤交给代码**——同 ADR-0004"LLM 判意图、代码记账"的哲学，也和本轮题型旋钮同构。
- 题型走**冻结映射**：LLM 只抽用户意图短语，代码用冻结同义表映射到既有三题型（"简答"→开放），短答意图**代码层
  禁止映射到选择题**，未知 / 缺省回落 `route_question_type` 自动路由。

一句话：**会话是无状态的对话前端，知识库是持久的全局单池；LLM 把自然语言意图翻译成 scope/题型选择，代码确定性地
选题、出题、判卷、记账。**

## User Stories

学习者视角：

1. 作为学习者，我想让 ingest 进来的所有材料进**同一个持久库**，这样换个启动词开新会话也能考到之前学的东西（修 #2）。
2. 作为学习者，我想在一次会话里用自然语言**在不同材料间切换**问答（"先考代理通信协议""再考 Hook"），不用每种材料开一个会话。
3. 作为学习者，我想说"考**代理通信协议**的题"，系统就只从这份材料出题，而不是从别的库乱考（修 #1 考错库）。
4. 作为学习者，我想说"出**简答题**"，就拿到开放式简答而不是选择题（修 #1 错题型）。
5. 作为学习者，当我要考的主题库里还没有时，我希望系统**诚实地说"还没有这个主题的材料，先 ingest"**，而不是硬考一个别的主题糊弄我。
6. 作为学习者，我想让它同时考**多份材料**（"把代理通信协议和 Hook 一起考"），scope 支持多选。
7. 作为学习者，我不用手动给材料打标签——ingest 时系统**自动**从内容里读出这份材料的主题名，直接进清单可选。
8. 作为学习者，我显式指定的题型应当**盖过**系统按记忆状态的自适应路由（我说了算），但不指定时仍按薄弱状态智能路由。
9. 作为学习者，我的出题语言偏好是**跨全库的个人设置**，不再挂在某个 task 上。

工程师 / 简历叙事视角：

10. 作为工程师，我要 scope 与有效题型都**上 AgentEvent 事件脊柱**，于是"考错库 / 错题型"这类行为 bug 能在 trace 上一句查询就现形、也能被 eval 断言。
11. 作为工程师，我要 scope 的语义匹配是 LLM 的活、过滤是 exact-id 代码的活——**不引入任何模糊子串匹配**，从而绕开 dict 版 vs SQLite 版的大小写 / 中文规范化 parity 陷阱，replay 逐字节稳。
12. 作为工程师，我要两个 Store 实现的 `all_items()` / scope 查询**保持 item_id 升序 parity**（选题 rng 按下标选，乱序即 replay 不对齐）。
13. 作为工程师，我要新增的 scope/题型参数一律**默认 None、加法式演进**，不指定时行为等价于改动前，既有确定性单测与 eval 用例逐字节不受影响。
14. 作为工程师，我要 scope/题型逻辑**全留在 domain/learning**，kernel 泛型 runtime 一行不碰（import-linter 门守住）。
15. 作为面试者，我要能对着代码讲"一个持久全局 KB + 自然语言 scope，如何靠'LLM 翻译意图、代码确定执行'既贴用户心智又不破 replay/eval 地基"。

## Implementation Decisions

四个分叉 + 形态在对话中锁定：

- **Task 模型（分叉1，B → 清库下收敛）**：`tasks` 表消解，**不再有独立 `LearningTask` 实体**。
  - `resource_id = derive_id(url)`（去掉 task_id 入参；内容寻址、同 URL 全局唯一 → INSERT OR REPLACE 天然去重）。
  - `resources` 新增可空 `topic` 软标签列（Reader 抽，见下），去掉 `task_id` 外键。
  - `item_id = resource_id#index` 资源内唯一不变（ADR-0002 边界不动，concept_key 二期缝保留）。
  - `language`（出题 / 判卷语言）从 task 属性**归入 Preference Memory**（本就有 `question_language` 偏好、能覆盖）——语言是跨库个人设置，不是材料属性。
- **Scope（分叉2，目录式）**：
  - `start_quiz` 增 `resource_ids: list[str] | None`（默认 None = 全库）。
  - Store 新增单一 canonical 全局读 `all_items()`（两实现 ORDER BY item_id 升序），把 assessment / tools / context / harness-natural 的读全部切过它。
  - 新增 domain 纯函数 `apply_scope(items, resource_ids)`：保序、成员归属过滤，落 selection.py、走 TDD；`select_target` 签名及其既有 caller 零改（scope 是 select_target 之前的上游预过滤）。
  - **目录注入**：`learner_context_provider` 扩一段"库存清单"——按 resource_id 升序渲 `{resource_id → topic}`，注入 ReAct 上下文；agent 不调工具即知库里有哪些材料。LLM 据此把用户意图映射成 exact resource_id 填入 `start_quiz`。命中不了 → 拿不到 id → 诚实拒答。
  - scope 空命中（非空 resource_ids 过滤后为空）→ 新 `ASSESSMENT_REFUSED(reason="empty_scope")`，在 select_target 之前；`resource_ids=None` 且全库空仍走既有 `empty_kb`。
- **题型（分叉3，A：LLM 抽意图 → 代码冻结映射）**：
  - `start_quiz` 增 `question_type` 意图字段（可选）；工具 description 教 LLM 只抽用户意图短语。
  - routing.py 新增**冻结同义映射表**：短语 → 既有三题型枚举（选择题 / 开放 / 追问），"简答"等短答意图 → 开放，**代码层禁止短答意图映射到选择题**；未知 / 缺省 → 回落 `route_question_type(state)`。
  - `assess_once` 在路由前：`effective = map(intent) if intent else route(state)`。复用既有三题型，**不新增第 4 题型 / 不新增 prompt / 不新增 grading 路径**（MC 仍确定性判卷、开放 / 追问仍 LLM+cassette）。
  - `QUESTION_ASKED` payload **同记 routed 与 effective** 两种题型，供 eval 断言"用户意图透传"。
- **Reader 抽 topic（RAG-metadata）**：Reader 深读一份材料时，结构化输出**再产一个资源级 `topic`**（"这份材料讲什么"的一句话），pydantic 校验、replay 确定，存入 `resources.topic`。这是目录清单的人类可读来源，用户免手动打标签。
- **旧数据（分叉4，清库重来）**：**不写数据迁移 SQL、不做薄弱账合并**。旧 dogfood 数据归档 / 丢弃，新库从新派生起步、重新 ingest。schema 直接落新形状（新 resources 表无 task_id / 有 topic，弃 tasks 表）。
- **cassette**：B 下 item_id 全线位移 → golden cassette 需**真机重录**（人机边界，用户真 key，走 `scripts/record_assess.py`）；Reader 加 topic 字段亦需重录 ingest 侧录制。
- **事件脊柱**：scope（有效 resource_ids + 命中数）与有效题型上 AgentEvent（`ASSESSMENT_STARTED` payload 补带判别力字段 / 或新 scope 事件，payload 不透明、kernel 泛型分发）。恒定的 task_id 判别力字段随之退役。
- **ADR**：出两篇（或合一）——① 全局 KB / task 消解（改写 CONTEXT.md 核心领域词 `LearningTask` 语义）；② 用户显式题型覆盖对 routing.py 书面契约"题型由代码定"的例外。
- **确定性 / 分层**：scope 匹配 / 选题 / 题型决策 / 记账全是确定性代码，LLM 只填意图（resource_id、题型短语）、被录进 completion → replay 稳；改动全落 domain/learning + interfaces/cli + evals，kernel 无感。

## Testing Decisions

好测试只断言**外部行为**（事件轨迹 / 判决 / 拒答 / 库可见性），不锁实现细节。缝（最高缝优先、几乎全用现有缝）：

- **缝1 Store（存储主缝）**：dict `LearningStore` vs `SqliteLearningStore` **parity**——`resource_id=derive_id(url)`、`resources.topic` 列、`all_items()` 全局读、按 resource_ids 过滤查询、目录列举，两实现结果**逐条相等 + item_id 升序**（含跨资源、不同 hash 前缀）。先例：现有 `tests/test_sqlite_store.py` 的双实现相等断言。
- **缝2 选题纯函数**：`apply_scope(items, resource_ids)` × focus(mixed/new/weak) × weak × asked 组合——保序、成员归属正确、空命中。纯函数红-绿-重构，先例 `tests/test_selection.py`。
- **缝3 题型冻结映射纯函数**：意图短语 → 枚举映射、"简答"→开放、**短答意图绝不 → 选择题**、未知回落自动路由。纯函数 TDD。
- **缝4 assess_once 编排（事件 + replay）**：resource_ids/question_type 参数透传、`empty_scope` 拒答分支、`QUESTION_ASKED` 记 routed+effective、scope-honor（请求 topic=X → 所有 QUESTION_ASKED.item_id 对应 concept 命中 X）。replay cassette + 事件流断言，先例 `tests/test_assessment.py`。
- **缝5 Reader topic 抽取（LLM 槽）**：不 unit-TDD，走 replay cassette——断言结构化输出含合法 topic、进 `resources.topic`。先例 Reader 结构化输出测试。
- **缝6 目录注入（确定性渲染）**：`learner_context_provider` 渲 `{resource_id→topic}` 清单为确定性字符串（按 resource_id 升序、空库→空段跳过）。先例现有 context 渲染测试。
- **缝7 工具装配**：`start_quiz` 收 resource_ids/question_type 并透传 assess_once。先例 `tests/test_react_quiz_tools.py`。
- **缝8 eval harness（Tier-1 规则）**：`build_stocked_store` 扩**多资源夹具**；新用例 scope-honor、empty_scope 拒答、question_type-honor（"简答"→ effective=开放、断言不出选择题）；既有 8+2 用例默认路径（不传新参）逐字节不变。
- **缝9 CLI 集成**：`run_react`/`run_quiz` 无-task 装配 + 目录接线，replay provider 驱动，先例 `tests/test_cli_react.py`。
- **不变量**：默认路径（resource_ids=None + question_type=None）message/replay_key/prompt 版本号一字不变；scope/题型/选题全程不碰判卷记账（`weak_item_id` 仍代码按 verdict 算）。

## Out of Scope

- **任意网络资源抓取（web fetch / web_search / MCP 融合）**：明确延后到独立阶段。当前 ingest 仍是文件式（`file://local/<path>`）。**本重构对它前向兼容**：`resource_id=derive_id(url)` 对 url 字符串不挑，将来 `https://…` 走同一派生、模型零改动；不可信输入防护（`neutralize_fence` 拦截器 + `trusted=False` + fetch 守卫）已就绪。URL 归一化去重亦属那一阶段。
- **数据迁移 / 薄弱历史保留**：清库重来，不做回灌 + 薄弱账合并（用户确认历史数据不重要）。
- **同义词 / 译名的代码级模糊匹配**：语义匹配是 LLM 的活（目录式 scope），代码只做 exact-id 过滤——刻意不写模糊子串 / 分词匹配。
- **context compression**（预算 / 历史滑窗 / 老轮摘要）：接 S3 留的 `Partition.budget` + `CompressionPolicy` 缝，**排在本重构之后**（用户要动手学）。目录清单是它的天然消费者。
- **per-material 语言**：语言归全局 Preference；若日后要按材料区分再加 `resources.language` 列。
- **R2 轨迹 eval + 迭代 CI gate、R3 Tier-2 LLM-judge + 自进化**：更靠后。
- **新增"简答"为第一类题型**：复用既有三题型，assert_never 缝留二期。

## Further Notes

- 基线 main `8450499`。设计经 15-agent 设计 workflow（映射 + OSS 调研 + 5 分叉设计 + 3 视角对抗评审）收敛，0 blocker 违规；四分叉在对话中经用户逐一拍板。
- OSS 参考形状（ADR-0001 照形状写在自有脊柱、不照搬）：全局池 + 查询期软 scope = Anki 牌组/标签/搜索、Pinecone/Mem0/LlamaIndex metadata filter；内容寻址身份 = git blob / Docker digest / IPFS CID / Nix store path；目录式"LLM 从枚举挑" = LlamaIndex auto-retrieval + Anthropic 可选枚举参数（后端声明词汇、缺省即代码策略、显式值覆盖）。
- 竖切按 tracer-bullet 拆（/to-issues），一个 PR 一个可验收行为、保 CI 五门全绿；建议序：先落模型重塑 + Store（含 parity 护栏）→ all_items 全局读（修 #2）→ Reader topic + 目录注入 → apply_scope + scope 参数（修 #1 库）→ 题型冻结映射（修 #1 型）→ eval 多资源夹具 + 三类新用例 → CLI 装配 + ADR/CONTEXT.md/docstring。真机重录 cassette 属人机边界。
