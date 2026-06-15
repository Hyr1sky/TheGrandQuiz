# ADR-0003: 记忆库从 roadmap 的四分库收为两类，MVP 只实现 Learning + Preference

- 状态：已接受
- 日期：2026-06-12

## 背景

roadmap 设计了四类分库：Session / Learning / Preference / Resource Memory。落到考核竖切
MVP 时发现两处问题：Resource Memory（资源摘要 + 概念 + 引用 + 质量判断）与 KnowledgeItem
（概念 + 摘要 + 证据 + 置信度）职责重叠，并存会让同一批数据存两份且语义不清；Session Memory
本质是 kernel 的会话历史，属 kernel 概念而非 domain 记忆。

## 决策

MVP 只实现两类领域记忆：

- **Learning Memory**：薄弱概念 × 表现历史，考核循环的持久层、选题优先级的唯一数据源。
- **Preference Memory**：用户偏好（题型偏好 / 追问强度 / 语言），带 confidence。

Resource Memory 并入 KnowledgeItem（不重复造实体）；Session Memory 归 kernel 的会话历史，
不作为 domain 记忆库。

## 后果

- 概念数据单一归宿（KnowledgeItem），grounding 与 eval 锚定不产生二义。
- 偏离 roadmap 明文四分库；未来若资源体量增长需要独立的资源级检索/质量记忆，
  再从 KnowledgeItem 中析出 Resource Memory（届时是有数据支撑的演进，而非预先抽象）。
- 与 [ADR-0002] 一致：记忆与考核都锚定 KnowledgeItem 作为概念同一性边界。
