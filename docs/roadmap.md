# TheGrandQuiz Roadmap

> 本文件只记录当前基座、下一阶段候选、进入条件和明确关闭项。已完成工作的过程与指标见
> [devrecords](devrecords/)，产品定义见 [product.md](product.md)，领域实体见
> [domain-model.md](domain-model.md)，架构约束见 [architecture.md](architecture.md) 与 [ADR](adr/)。

## 当前稳定基座：v0.5.0

TheGrandQuiz 已形成可运行的 local-first 学习闭环：

```text
用户授权材料
→ 修订化文档结构与精确 Evidence
→ 有界材料对话 / 逐题考核
→ 判决与用户纠正
→ 薄弱状态记账
→ 下一轮优先复考
```

当前稳定能力包括：

- Markdown/Text/公开 URL 导入、Web Search 候选、人工审批与原子 KB 提交；
- ResourceRevision、DocumentNode、FTS5、精确 Evidence 与渐进式 Agentic Search；
- CLI/Web 材料对话、选择题/开放题考核、用户补充申诉和三态 Learning Memory；
- 长期 LearningFact Journal、可重建 AssessmentAttempt/LearnerProjection 与人工审核数据入口；
- 桌面 Web 语音录制、ASR 草稿审查、材料术语提示和唯一 Assessment 提交；
- AgentEvent 事件脊柱、Trace、Record/Replay、Recovery、离线 Eval 与安全 Web 投影。

版本能力和已知限制见 [v0.5.0 Release Notes](releases/v0.5.0.md)。实现证据不在 roadmap 重复。

## 已完成的当前工程收口：Eval Harness E0–E4

Eval 是项目的承重卖点。E0–E4 已在不改变 case、cassette、评分或报告语义的前提下完成结构深化：

1. `harness.py` 只保留稳定 facade 与 CLI 入口；
2. `solvers.py / runner.py / reporting.py` 按真实变化原因拆分；
3. conformance tests 固定 17-case manifest、唯一 Tier-2 归属、报告产物和 ReplayMiss 硬失败；
4. Tier-1 / Tier-2 的 trace、verdict 与 execution/judge cost 继续分列；
5. Replay 仍是普通测试和默认报告的唯一模型路径。
6. typed per-kind observation 已替换 `SolveResult.context`，错误 case/observation 组合在构造点失败；
7. Search/Fetch Replay profile 归 case 所有，录制、回放与包内资产审计共用同一声明，孤立资产在 CI 失败。

## 最近完成：Eval-guided Evolution E5–E8

该周期已把 Harness、Grading Benchmark、Eval Inbox 与真实纠正数据连成受控改进回路：

1. **E5 — Coverage 与 Subject Identity**：建立 Eval Surface 覆盖矩阵，并以不可变
   Eval Subject Snapshot 冻结 prompt、model/provider/thinking、tool schema 与关键策略身份；
2. **E6 — Calibrated Semantic Quality**：优先修复 Holdout 03 暴露的判卷语义召回缺口，再逐个为
   question quality、reader fidelity 与 grounded answer 增加经过人工 calibration 的质量门；
3. **E7 — Paired Experiment**：在同一 Dataset Snapshot 上比较 baseline/candidate，分列规则、语义、成本、
   延迟、稳定性和失败切片，不用两个独立总分冒充因果比较；
4. **E8 — Human-gated Promotion**：允许系统从真实纠正与失败证据提出候选并自动运行 Development Eval，
   但只有人工决策和新的未见 Release Holdout 可以晋升，所有版本保留回滚身份。

本周期不交付无监督自动改 prompt、自动晋升、通用 plugin runtime 或 Provider 自动路由。完成 E5–E8 后，
下一条产品主线默认进入 Material Channels。E8 完成表示控制契约与 bypass tests 已落地，不表示已有真实
候选通过新 Release Holdout 并激活；第一次真实晋升仍是显式 HITL。

## 当前工作焦点：Material Channels 立项

下一轮先把用户有权访问、但服务端难以直接抓取的材料接入现有 Acquisition → Reader → 审批 → KB
流水线。实现前需形成独立 PRD/ADR 与竖切 issues；Provider Profiles、Voice/TTS 和学习主页保持候选，
不与 Channel 基座并行铺开。

## 下一阶段产品候选

候选按当前产品增益排序。每项在实现前单独形成 PRD 与可验收竖切，不一次铺满。

### P1：Material Channels

解决微信、知乎、登录页等“服务端无法可靠抓取，但用户有权阅读”的输入问题。新增用户授权的输入 Channel，
统一产出规范化 ImportedArtifact，再进入现有 Acquisition/Reader/审批流程。

优先考虑：粘贴文本、HTML/PDF/导出文件、浏览器扩展或系统分享入口、GitHub 文档。Channel 不绕过
不可信输入标记、大小限制、人工审批或精确 Evidence；不以对抗平台反爬为目标。

### P2：学习主页、轨迹与知识管理

把已有学习事实变成日常入口，而不是增加新的模型判断：

- 继续上次学习、最近 Assessment、薄弱/观察中/销账状态；
- 按材料、KnowledgeItem、revision 和 Evidence 浏览；
- 从薄弱项显式发起下一轮复考；
- 安全资源操作、数据位置和备份说明。

第一阶段不引入连续掌握度分数、提醒日历或复杂 spaced repetition。进入条件：当前 LearningFact、
LearnerProjection 和资源查询可以有界投影，无需新建第二套学习状态。

### P3：Provider Profiles 与能力注册

在现有 basic/enrich 角色、OpenAI-compatible Provider、dialect、thinking 与 Replay identity 之上，增加：

- 显式 ModelProfile；
- tools、streaming、structured output、reasoning 等 ProviderCapabilities；
- 用户选择角色 profile，安全配置版本进入 Trace；
- 密钥继续只由环境变量管理。

自动模型路由与 fallback 必须后置到 routing eval、成本预算和失败策略明确之后；不能先做黑盒自动选模型。

### P4：Voice Interview 的 TTS 阶段

先把“题目朗读 → 口头作答 → 草稿审查 → 判卷”做完整，再评估实时双工和数字人：

1. 浏览器或 Provider-neutral TTS；
2. 朗读、重播、语速与口头作答节奏；
3. 多轮 Interview Session；
4. 真实体验证明价值后，才讨论实时字幕、打断、双工与数字人形象。

## 技术候选

- 删除无生产消费者的旧 `AssessmentWorkspace`，保留单一 Web 考核实现；
- Trace token usage projector 已由 Chat、Observatory 与 Eval 共享；
- Reader 吞吐只在 trace profiling 证明瓶颈后进入有界并发；
- 当出现第二个真实 subagent 时，再抽通用 `kernel/subagent.py`；
- Responder 跨进程 suspend/resume 随 AssessmentSession 持久化需求一起设计。

## 继续关闭或受 Gate 阻挡

以下方向没有新的消费者或真实 Eval 增益前不进入默认产品路径：

- KnowledgeRelation、CanonicalConcept 与自动跨资源归并；
- 自动 Demand Judge、自动 ambiguity/clarification classifier；
- Required Claims 默认判卷和任意 Boolean rubric schema；
- 无监督自动改 prompt 或自动晋升数据；
- GraphRAG、向量库、图数据库和重型 RAG 运行栈；
- 没有 profiling 证据的 Reader 并发、后台定时抓取和主动通知；
- 实时双工语音、移动端语音与数字人形象。

## 排序纪律

1. 先证明用户或 Eval 消费者，再增加 schema、Adapter 或后台状态机。
2. 核心考核继续是确定性 workflow；自由 ReAct 只承担开放编排。
3. 新基础设施复用 AgentEvent、Trace、Replay 和现有审批门，不另建平行事实源。
4. 一个竖切对应一个可独立验收行为；具体执行状态只记录在 `.scratch/CURRENT.md` 引用的 PRD 中。
