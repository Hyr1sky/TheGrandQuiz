# 目标架构

> 状态：框架已与产品负责人对齐（2026-06-12），细节设计随需求讨论迭代。
> 产品层面的领域模型 / Subagent / 工具规划见 [roadmap.md](roadmap.md)。

## 核心设计判断：事件总线是脊柱

hook、trace、流式输出、eval replay **不是四个独立模块，而是同一条事件流的四个消费者**：

- Runner 在每个生命周期节点发射结构化 `AgentEvent`
- **trace** 是事件的持久化
- **hook** 是事件的订阅者
- **流式输出（SSE / CLI）** 是事件的网络投影
- **eval replay** 是事件的回放

五大基建模块由此共享同一地基，而不是五套各自为政的回调系统。

## 核心设计判断（二）：核心路径是 workflow，自由 ReAct 只用于开放编排

"可评测 / 可恢复"的代价，是把**值得 eval 的路径做成确定性 workflow**，而不是让 LLM 自由 agency。
很多 agent 项目死在"什么都做成自由 ReAct"，然后没法 eval、没法复现。本项目反过来：

- **核心考核循环是 workflow**：选题 → 出题 → 答 → 判卷 → 状态转移 → 下一题。LLM 只在两个有界的槽里被调用——**生成一道题**、**判一次卷**；其余步骤是确定性代码。
- **自由 ReAct 只保留给开放式编排**：用户随口提问、agent 决定要不要去深读 / 列资源 / 进入考核。开放对话要灵活，核心循环要可复现，两者分开。

### LLM 判卷，确定性代码记账

| 决策 | 谁做 | 为什么 |
| --- | --- | --- |
| 判一道题 对/勉强/错 | LLM（工具，结构化输出 + 证据锚定） | 语义判断，无法确定性化 |
| 生成一道带证据的题 | LLM（工具，grounded on 选中的 KnowledgeItem） | 同上 |
| 薄弱概念状态转移（错→薄弱→观察中→销账） | **代码** | 判决的确定性后果；LLM 来做则 replay / eval 对不齐 |
| Learning Memory 写入 | **代码** | 同上——eval case 4/6 可断言性的命门 |
| 选题候选集构造（薄弱优先） | **代码** | eval case 5 可断言性的命门 |
| 候选集内选哪道题 | LLM（保留灵活） | 候选集已按薄弱优先构造，最终挑选的自由不破坏 weak-first 不变量 |
| Preference Memory 写入 | LLM（after_turn 判断值得记什么） | 偏好是软信号，可容忍非确定性 |

### subagent 与 tool 的划分

判据硬性：**subagent = 需要隔离大上下文 + 输出可结构化验证**；**tool = 单次有界调用**。

- MVP 唯一 subagent：**Reader**（深读长文档，隔离那一大坨上下文，产出 KnowledgeItem[]）。
- 出题、判卷是 **tool**（带 pydantic schema 的单次调用）——做成 subagent 等于把简单调用包成重量级隔离上下文。
- roadmap 的 6-subagent 表是二期候选，按此判据逐个再立项，不为"多智能体"而多智能体。

## 分层结构

```text
src/grandquiz/
├── kernel/                  # 通用 Agent Runtime（禁止 import domain，import-linter 强制）
│   ├── events.py            # AgentEvent 类型体系（整个系统的数据脊柱）
│   ├── runner.py            # ReAct 循环（自 scholarmate 移植 + 事件化改造）
│   ├── tools.py             # Tool / ToolRegistry（移植）
│   ├── hooks.py             # HookManager：interceptor + observer 两类
│   ├── context.py           # ContextBuilder：分区拼装 + token 预算
│   ├── memory.py            # Memory 抽象接口（store / recall / policy）
│   ├── recovery.py          # 错误分类法 + RecoveryPolicy
│   ├── trace.py             # TraceStore（事件持久化，span 树结构）
│   ├── subagent.py          # Subagent 执行器（隔离上下文 + 并发控制 + 结构化输出契约）
│   └── approval.py          # 人工审批门（计划：暂停 / 恢复 turn 的通用原语）
├── providers/
│   ├── llm.py               # OpenAICompatProvider（移植）+ DemoEchoProvider
│   ├── replay.py            # Record/Replay Provider（eval 确定性的基石）
│   └── usage.py             # token 用量 / 成本核算
├── domain/learning/         # 学习领域（roadmap.md 中 learning/ 的全部内容）
├── interfaces/              # 可插拔通道，产品形态不绑定 Web
│   ├── api/                 # FastAPI（REST + SSE）
│   ├── cli/                 # CLI REPL 聊天客户端 + trace 查看器（开发期主力界面）
│   └── asr/                 # 语音（移植 asr_ws.py）
└── evals/
    ├── cases/               # 用例 DSL（YAML）
    ├── graders/             # 规则断言 + LLM judge
    └── harness.py           # 运行器 + 报告
```

## 五大基建模块设计要点

### Hook 体系

区分两类语义，不混用：

- **interceptor**（`before_*`）：可修改入参、可阻断。审批门、注入防护挂在这里。
- **observer**（`on_*` / `after_*`）：只读旁观。trace、memory 写入挂在这里。

Hook 抛异常必须被隔离，不能炸掉整个 turn。

### 上下文管理

1. ContextBuilder 按分区（system / persona / memory / knowledge / history）拼装，每区有 token 预算
2. **跨轮次裁剪**：历史只保留最终 assistant 回答，丢弃 tool 调用中间过程（scholarmate 已知 TODO，新仓库第一天做对）
3. 工具结果截断策略 + 渐进式披露：先给摘要，模型要详情再展开（scholarmate 的 catalog 模式已验证）

### 文档结构与精确溯源（ADR-0008，DS-S1–S4 已实现，真实回放待收口）

学习材料不再只以完整 `raw_content` 和一次性 Reader token 分块存在。每个获批内容版本形成不可变
`ResourceRevision`，并由确定性 parser 建立 `DocumentNode` 树；Reader、ReAct、Summarizer 与 eval 共享同一个
Document Structure module，而不是各自切分和定位正文：

```text
LearningResource（稳定 locator）
  └── ResourceRevision（不可变 content_hash 版本）
        └── DocumentNode tree（确定性结构 + source span + FTS）
              └── Evidence（revision + node + 精确 span）
                    └── KnowledgeItem（学习 / 考核身份）
```

三类关系严格分层：DocumentNode 父子边只表达原文结构；KnowledgeItem 到 Evidence / DocumentNode 的边表达
可校验 grounding；KnowledgeItem 之间的 prerequisite / related / contradicts 才是带置信度、provenance 与
eval 门控的语义关系。`section_path` 用于 LLM 和用户导航，不作为节点身份。第一阶段用 SQLite adjacency rows、
recursive CTE 与 FTS5，不引入向量库、图数据库或 Knowhere 重运行时。

ingest Reader 按树的自然节点确定性覆盖材料，保留核心 workflow；开放 ReAct 才让 LLM 执行“大纲 → 搜索 →
展开 → 精确正文”的 Agentic Search。所有解析、搜索、节点选择、预算与 citation 都上同一条事件脊柱。

当前已交付不可变 ResourceRevision、确定性 Markdown/纯文本 DocumentNode parser、精确 Evidence 与历史
citation 解析、自然节点覆盖型 Reader，以及 current-only FTS5 / 大纲 / 搜索 / 展开 / 有界读取 / read-before-cite
工具。revision/tree/items/evidence/FTS 共享原子提交；显式资源 scope 解析失败时零读取并 fail closed。parser、
Reader 批次、搜索、读取和 citation 事件全部进入同一事件脊柱，kernel 仍保持领域无关。生产库已无损迁移到
schema v11；两份受 prompt/tool schema 影响的真实 cassette 重录前，代码交付不等于五门已全绿。

### 记忆系统

两类领域记忆 **Learning + Preference**（见 ADR-0003；Resource Memory 已并入 KnowledgeItem，Session 归 kernel 会话历史），SQLite + JSON 实现。关键机制：

- **写入策略**：Preference Memory 挂 `after_turn`、由 LLM 判断值得记什么；**Learning Memory 写入是判决的确定性后果（代码），不走 LLM 判断**。
- **召回策略**：区分两种召回——(a) 把记忆喂进 LLM 上下文（让考官知道你弱在哪），ContextBuilder 按当前 LearningTask 查询带 confidence 过滤；(b) 确定性地构造下一题的薄弱优先候选集（代码，见核心设计判断二）。两者都在，机制不同。

### 错误恢复

- 先建错误分类法（`ErrorClass` 枚举：参数无效 / 网络 / 资源不可读 / 超时 / 预算耗尽 / …），每类映射一个策略（修复参数 / 退避重试 / 标记失败换源 / 返回部分结果 / 升级人工）
- **错误本身是一种 AgentEvent**，自然进 trace——错误不只是字符串还给模型

### Eval harness（trace + grader）

1. **先定 trace schema 再写功能**：`turn_id / span_id / parent_span / type / input / output / tokens / latency / error`，span 成树（turn → model_call → tool_call → subagent）。Schema 就是 eval 的数据契约。
   - **事件是信封，领域事件上同一条脊柱**：`AgentEvent` = `type`（字符串）+ 元数据 + 不透明 payload。kernel 泛型地分发 / 持久化，不认识具体类型；domain 在自己那层定义领域事件（ResourceApproved / ItemCreated / AnswerJudged / ConceptStateChanged）与 payload schema，经 kernel 的 `emit()` 发射。kernel 保持领域无关（分层守卫不破），eval 又能在 trace 上断言领域行为（case 4/5/6 断言的都是领域事件）。
2. **回放是事件流回放，不只是 LLM 回放**：所有外部 I/O（LLM / fetch / 时钟 / 随机）都是非确定性边界，统一走工具、结果作为事件落在脊柱上——回放 = 重放事件流，LLM Record/Replay Provider 只是其中一个特例。录制按 messages 哈希落盘，回放直接命中，eval 不烧 token、完全确定。
3. Grader 两层：**规则断言**（工具调用顺序、审批门、引用存在性）跑在 trace 上；**LLM-as-judge**（grounding、回答质量）跑在最终输出上。行为 eval（规则断言）是 MVP；质量 eval（LLM-judge + golden set，判卷准不准）是更深一层，二期。
4. **部分 eval 断言同时是运行时不变量**：如"出题必须锚定存在的 KnowledgeItem 且 evidence 非空"（case 3），应在出题工具产出后有一道确定性校验门挡住幽灵题再展示，而非只在 eval 里查。

## 工程性模块（一等公民，非可选项）

| 模块 | 要点 |
| --- | --- |
| **注入防护** | 学习 agent 读网页 / GitHub，抓回内容是不可信输入。工具结果打"不可信"标记 + system prompt 硬约束 + fetch 层做大小 / 超时 / 域名限制。学习场景相对学者场景**新增的攻击面**，进 MVP |
| **结构化输出契约** | subagent 与 LLM 工具（出题 / 判卷）的返回结果用 pydantic schema 强制校验，失败自动重试——"output can be verified" 的落地机制 |
| **中断与取消 / 审批挂起** | 长 turn（深度阅读 40s+）的用户中断、优雅终止、半成品落 trace。当前已交付阻塞 CLI 筛选，并发 `approval.requested/decided`；目标形态仍是可挂起 / 可恢复 turn（持久待决状态 + token），该能力尚未实现，不能把同步协议当成 suspend/resume |
| **确定性基建** | 时钟 / 随机数走注入（`Clock` 抽象 + 种子化 RNG），否则 replay 永远对不齐。第一天避开这个坑 |
| **Token / 成本核算** | 每 turn 用量进 trace，eval 报告带成本列 |
| **SQLite 迁移** | 版本号 + 顺序 SQL 文件，不上 alembic |
| **Prompt 版本管理** | prompt 模板独立于代码存放，trace 记 prompt 版本号，eval 回归可归因 |

## 搭建顺序（trace 先行 + 竖切拉动，2026-06-12 修订）

> 修订背景：MVP 定位为"考核竖切"（见根目录 CONTEXT.md 与 roadmap MVP Scope）。
> 排期改为走骨架（walking skeleton）：trace/replay 作为脊柱与确定性地基先行，随后立即拉一条
> 最小可跑的考核竖切穿透全栈，hook / context / recovery / memory 由真实 domain 拉动着逐层加硬，
> 而非自底向上把六层 kernel 抽象建完再上 domain（避免为 domain 不需要的能力做过度抽象）。

```text
0.  建仓 + 脚手架 + 工程规范        → 验证：CI 全绿的空项目              ✅ 2026-06-12
1.  移植核心 + 事件化改造 runner     → 验证：CLI REPL 能和无工具 agent 对话 ✅ 2026-06-18
2.  TraceStore + Replay Provider    → 验证：一次对话可完整回放             ✅ 2026-07-04
3.  考核竖切（走骨架）              → 验证：手喂 URL → 深读 → "考我" → 判卷，一条链路跑通并落 trace ✅ 2026-07-05
        ├ 手动喂 URL 的 mock 资源源
        ├ Reader subagent 深读产出 KnowledgeItem
        ├ 审批门（深读产出 → 入库）
        ├ 出题（题型路由）+ 判卷（判决三值 + 证据锚定）
        └ Learning Memory（薄弱概念入库 / 销账）
4-7. 由竖切拉动逐层加硬 kernel：                                      ✅ 2026-07-08
        HookManager（审批门 / trace 写入挂上去）
        ContextBuilder + 跨轮裁剪（多轮考核 token 不膨胀）
        RecoveryPolicy + 错误分类（深读 fetch 失败走标记/换源）
        Memory 接口规整 + SQLite（Learning + Preference 跨会话生效）
8.  Eval harness                    → 验证：CONTEXT.md / roadmap 的考核竖切用例跑通 ✅ 2026-07-06
```

步骤 3 是产品的脉搏，越早可跑越好；步骤 4-7 不是新需求，而是把竖切里临时凑合的 kernel 部件
替换成正式实现，每一层都有真实 domain 调用方在压测它。

## 已确认决策（2026-06）

- 后端优先，产品形态不绑定 Web；前端不迁移，开发期用 CLI REPL
- 语音（ASR）链路保留在路线图，但加硬门（2026-06-12）：只在"面试 subagent"立项时一起立项
  （口头面试模拟是语音唯一站得住的场景），不进 MVP、不占近期优先级；persona 降为
  system prompt 中的考官语气设定，不做形象
- 旧仓库不减负、不改动，作只读参考（ADR-0001）
- 旧仓库泄漏的 DashScope key 需轮换，新仓库密钥只走 `.env`
