# M3.3 — 薄弱记忆 + 状态机 + 薄弱优先复考

Status: done（三态状态机 + 薄弱优先 + eval 4/5/6 完成，commit 4932a04，CI 全绿）
Type: AFK

> **纯确定性、无人机边界**：本增量全是代码（状态机 / 选题 / 销账），TDD 完成，无需真机调优。
> Learning Memory 仍是 dict 假件（台账 #1，SKELETON M7）——跨会话持久化留给 M7。

## Parent

[PRD: 考核竖切 MVP](../PRD.md)

## What to build

让考核闭环——判决驱动确定性记账，复考薄弱优先。判决（错 / 勉强）→ 确定性状态机写 Learning Memory（薄弱概念锚定 KnowledgeItem id，三态 + 连对计数）。状态转移：

```text
错|勉强            → 薄弱
薄弱   + 答对      → 观察中
观察中 + 答对      → 销账（移出薄弱表）
任一态 + 错|勉强   → 打回薄弱
```

即"连续答对两次才算掌握"，防蒙对 / 刚看完的假掌握。复考时**代码**构造薄弱优先候选集（有薄弱概念时新概念不进集），LLM 在集内挑题。发 ConceptStateChanged 领域事件。Learning Memory 本阶段可用 dict 顶着（M7 换 SQLite）。

体现 ADR-0004："LLM 判卷，代码记账"——状态转移 / 选题 / 销账全是确定性代码。

## Acceptance criteria

- [ ] 答错 / 勉强 → 对应薄弱概念按 item id 写入 Learning Memory（eval case 4）
- [ ] 复考出的题 ∈ 代码构造的薄弱优先候选集；有薄弱概念时新概念不进集（eval case 5）
- [ ] 答对一次 → 薄弱转"观察中"（仍在表内）；连续第二次答对 → 销账移出（eval case 6）
- [ ] 任一状态再答错 / 勉强 → 打回薄弱
- [ ] 发 ConceptStateChanged 领域事件进 trace
- [ ] 状态机、候选集构造、销账记账有单元测试（TDD，缝 2）
- [ ] eval case 4、5、6 在事件 / trace 流上可断言
- [ ] CI 全绿

## Blocked by

- [04 — 单题考核竖切](04-single-question-assessment.md)
