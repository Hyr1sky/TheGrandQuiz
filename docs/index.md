# 文档导航与权威边界

这份索引回答两个问题：某类信息应该去哪里找，以及同一件事出现冲突时应以哪里为准。

## 从哪里开始

| 目标 | 首选文档 |
| --- | --- |
| 第一次使用产品 | [README](../README.md) |
| 理解产品为谁解决什么问题 | [产品定义](product.md) |
| 查一个领域术语的准确含义 | [领域语言](../CONTEXT.md) |
| 理解实体、状态和不变量 | [领域模型](domain-model.md) |
| 理解代码分层与事件脊柱 | [架构](architecture.md) |
| 了解下一步而不是过去做过什么 | [路线图](roadmap.md) |
| 理解不可逆决策及其理由 | [ADR](adr/) |
| 复盘某次实现、验收和 dogfood | [开发记录](devrecords/) |
| 准备小范围试用 | [小范围 RC 指南](guides/rc-small-cohort.md) |

## 文档职责

| 文档 | 负责 | 不负责 |
| --- | --- | --- |
| `README.md` / `docs/guides/` | 安装、操作和用户可见能力 | 内部实体的完整字段 |
| `docs/product.md` | 用户、问题、核心循环、产品原则与非目标 | 代码结构和实现历史 |
| `CONTEXT.md` | 统一术语及易混淆概念 | 发布状态、实现清单和排期 |
| `docs/domain-model.md` | 领域对象、数据契约、状态与不变量 | 组件归属和部署拓扑 |
| `docs/architecture.md` | 分层、依赖方向、运行时机制 | 产品优先级和历史流水账 |
| `docs/adr/` | 已接受且代价较高的决策 | 尚未达成共识的想法 |
| `docs/roadmap.md` | 未来阶段、验收门和明确 backlog | 已完成工作的详细过程 |
| `docs/devrecords/` | 某轮工作的证据、过程与结果 | 当前规范 |
| `.scratch/<feature>/` | 本地 PRD、issues、探索和待 Grill 草案 | 提交到仓库的产品承诺 |

## 冲突时的优先级

1. 已接受的 ADR 约束架构级不可逆决策。
2. `CONTEXT.md` 约束领域用词，`docs/domain-model.md` 约束当前数据语义。
3. `docs/architecture.md` 约束组件职责和依赖方向。
4. README、指南和路线图应引用上述事实，不另造第二套定义。
5. 开发记录只说明“当时发生了什么”，不能覆盖当前规范。

发现冲突时应修正文档漂移；不能仅凭较新的日期默认覆盖已接受 ADR。

## 当前文档整理状态

Learning Model v2 基础闭环、受控词表、分类审核、可重建 Attempt/LearnerProjection 与稳定审查导出已经
实现；v0.2 又完成 Evidence locator、Acquisition 错误信封、AssessmentPlan 与 QuestionSpec 收口。
稳定模型见 [领域模型](domain-model.md)，长期事实边界见
[ADR-0010](adr/0010-durable-learning-facts-separate-from-operational-trace.md)。自动 Demand Judge、
AnswerDiagnosis/Misconception 晋升、新学习指标和知识关系仍受 Eval/消费者 gate 限制，不能因字段已写入
蓝图就描述成已交付能力。
