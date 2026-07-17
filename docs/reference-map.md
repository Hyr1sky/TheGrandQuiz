# 参考实现映射

新仓库以提取式迁移建立（见 [ADR-0001](adr/0001-extract-not-slim.md)），移植文件丢失原 git blame，
本文档记录每个模块的出处与外部参考。

## 主参考：scholarmate-digital-human

仓库：<https://github.com/KOP2020/scholarmate-digital-human>（本机路径
`~/桌面/DevStation/scholarmate-digital-human`，冻结为只读参考）。
手写 ReAct 循环 + 动态工具挂载 + 渐进式上下文披露 + subagent + 任务持久化的完整可跑通实现。

### 移植清单（约 1100 行）

| 新仓库目标 | 参考文件（apps/backend/scholarmate_dh/） | 行数 | 移植方式 |
| --- | --- | --- | --- |
| `kernel/runner.py` | `agent/runner.py` | 217 | 移植 + **事件化改造**（发射 AgentEvent） |
| `kernel/tools.py` | `agent/tools.py` | 92 | 近原样移植（零领域依赖） |
| 会话持久化 | `agent/session.py` + `integrations/storage.py` 的 `ConversationSessionStore` / `SqliteRepository` | ~250 | 移植，剥离学者仓库类 |
| `providers/llm.py` | `integrations/llm.py` | 444 | 移植（OpenAICompatProvider + DemoEchoProvider，max_tokens clamp） |
| 日志配置 | `logging_config.py` | 113 | 近原样移植 |
| `interfaces/asr/` | `api/asr_ws.py` + 前端 `hooks/use-asr.ts`、`public/audio-worklet-processor.js` | ~300 | 后端先移植；前端采集侧待产品形态确定 |

### 只看不搬（模式参考）

| 模式 | 参考位置 | 对应新模块 |
| --- | --- | --- |
| 多模态 subagent + 并发 batch | `agent/read_agent.py`（asyncio.gather + Semaphore） | Reader Subagent |
| 渐进式披露（摘要先行，按需展开） | `agent/catalog.py` + orchestrator 的 abstract_refs | ContextBuilder |
| 工具行为硬约束写法 | `agent/context.py` 的 system prompt 构建 | prompt 模板 |
| 异步任务 + 前端轮询 | `services/tasks.py` + `POST /digital-humans/async` | 长任务通道 |
| 引用卡片数据结构 | `components/chat/reference-card.tsx` 的 parseReferences | Source Citation |

### 已知坑（不要带过来）

- 工具两种风格并存（inner class 闭包 vs 顶层 class 注入）——新仓库统一用 `build_*_tools(deps)` 工厂
- 跨轮次保留 tool 调用中间过程导致 context 膨胀——新仓库第一天做对裁剪
- `PUT /control-config` 绕过 service 直接操作 repository——新仓库严守分层
- API key 写进 CLAUDE.md 进了 git 历史——密钥只走 `.env`

## 外部参考仓库

各取一瓢，不整体采纳任何框架（保持手写 runtime 的可控性与学习价值）：

| 仓库 | 借鉴什么 |
| --- | --- |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | tracing 的 span 模型与 processor 管线；guardrails（输入/输出拦截）与 handoff 的接口形状 |
| [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) | hook 体系设计（PreToolUse / PostToolUse 等生命周期点、matcher、可阻断语义） |
| [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai) | eval harness 金标准：Task / Solver / Scorer 分离，eval 日志与 replay 视图 |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 类型驱动的工具签名与结构化输出校验重试；`pydantic-evals` 的 case/dataset 组织 |
| [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) | 层级树作为检索入口，“读大纲 → 选择分支 → 展开自然章节”的 vectorless Agentic Search；节点标题、摘要、顺序与 source range 共同服务可解释导航。借鉴检索行为，不照搬实现或“完全不分块”的宣传口径——超大自然节点仍须在代码预算内按段落生成 synthetic children |
| [abhigyanpatwari/GitNexus](https://github.com/abhigyanpatwari/GitNexus) | 先确定性抽取结构节点/边，再把图变成 context、impact、trace 等查询能力；借鉴“结构图是查询基座而非可视化摆设”。代码 AST 图不直接类比散文语义图，我们只把确定性的 document hierarchy 放在高信任层 |
| [Ontos-AI/knowhere](https://github.com/Ontos-AI/knowhere) | 保留 section hierarchy、跨文档关系与 evidence-based citation；规则式实体/关键词重叠可作为二期 concept_key 候选配方。**不要带过来**：重运行时（Postgres/Redis/S3/worker/FastAPI monorepo）、向量库、GraphRAG 式实体抽取 + 社区检测、MinerU/VLM 多模态栈、大规模跨文档图导航 |
