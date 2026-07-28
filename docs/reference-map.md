# 公开参考映射

本文档只记录 TheGrandQuiz 当前架构使用的公开外部参考。项目自身的不可逆决策由
[ADR](adr/) 解释，当前代码与测试才是实现真源；参考仓库不构成依赖，也不意味着整体采纳。

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
