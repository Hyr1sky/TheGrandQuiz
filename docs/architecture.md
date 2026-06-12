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
│   └── approval.py          # 人工审批门（暂停 / 恢复 turn 的通用原语）
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

### 记忆系统

四类分库（Session / Learning / Preference / Resource，定义见 roadmap.md），SQLite + JSON 实现。关键机制：

- **写入策略**：挂在 `after_turn` hook 上，由 LLM 判断本轮有无值得记的内容
- **召回策略**：ContextBuilder 按当前 LearningTask 查询，带 confidence 过滤

### 错误恢复

- 先建错误分类法（`ErrorClass` 枚举：参数无效 / 网络 / 资源不可读 / 超时 / 预算耗尽 / …），每类映射一个策略（修复参数 / 退避重试 / 标记失败换源 / 返回部分结果 / 升级人工）
- **错误本身是一种 AgentEvent**，自然进 trace——错误不只是字符串还给模型

### Eval harness（trace + grader）

1. **先定 trace schema 再写功能**：`turn_id / span_id / parent_span / type / input / output / tokens / latency / error`，span 成树（turn → model_call → tool_call → subagent）。Schema 就是 eval 的数据契约。
2. **Record/Replay Provider 第一批做**：录制模式把 LLM 响应按 messages 哈希落盘，回放模式直接命中——eval 不烧 token、完全确定性。
3. Grader 两层：**规则断言**（工具调用顺序、审批门、引用存在性）跑在 trace 上；**LLM-as-judge**（grounding、回答质量）跑在最终输出上。

## 工程性模块（一等公民，非可选项）

| 模块 | 要点 |
| --- | --- |
| **注入防护** | 学习 agent 读网页 / GitHub，抓回内容是不可信输入。工具结果打"不可信"标记 + system prompt 硬约束 + fetch 层做大小 / 超时 / 域名限制。学习场景相对学者场景**新增的攻击面**，进 MVP |
| **结构化输出契约** | subagent 返回结果用 pydantic schema 强制校验，失败自动重试——"output can be verified" 的落地机制 |
| **中断与取消** | 长 turn（深度阅读 40s+）的用户中断、优雅终止、半成品结果落 trace |
| **确定性基建** | 时钟 / 随机数走注入（`Clock` 抽象 + 种子化 RNG），否则 replay 永远对不齐。第一天避开这个坑 |
| **Token / 成本核算** | 每 turn 用量进 trace，eval 报告带成本列 |
| **SQLite 迁移** | 版本号 + 顺序 SQL 文件，不上 alembic |
| **Prompt 版本管理** | prompt 模板独立于代码存放，trace 记 prompt 版本号，eval 回归可归因 |

## 搭建顺序（按依赖关系）

```text
0. 建仓 + 脚手架 + 工程规范        → 验证：CI 全绿的空项目          ✅ 2026-06-12
1. 移植核心 + 事件化改造 runner     → 验证：CLI REPL 能和无工具 agent 对话
2. TraceStore + Replay Provider    → 验证：一次对话可完整回放
3. HookManager（事件订阅）          → 验证：no-op hook 全链路触发可见
4. ContextBuilder + 跨轮裁剪        → 验证：多轮对话 token 不线性膨胀
5. RecoveryPolicy + 错误分类        → 验证：模拟网络失败走重试路径
6. Memory 接口 + SQLite 实现        → 验证：偏好记忆跨会话生效
7. Learning domain MVP             → roadmap Phase 1（模型 / 审批门 / 工具）
8. Eval harness                    → 验证：roadmap 中 6 个初始用例跑通
```

与 roadmap.md 原阶段划分的唯一差别：trace / 事件 / replay 从 Phase 3-4 **提前到最前**——
hook、recovery、eval 全部建在它上面（roadmap 末尾主建议本就如此，此处对齐执行顺序）。

## 已确认决策（2026-06）

- 后端优先，产品形态不绑定 Web；前端不迁移，开发期用 CLI REPL
- 语音（ASR）链路保留并移植
- 旧仓库不减负、不改动，作只读参考（ADR-0001）
- 旧仓库泄漏的 DashScope key 需轮换，新仓库密钥只走 `.env`
