# M8-fix② — 无重复出题（代码记账的已问过台账 + 去重门）

Status: ready-for-agent
Type: AFK

> 修真机 dogfood 暴露的"连续两轮题目内容完全相同"。复考锁定薄弱概念是设计意图（ADR-0003），
> 要修的是"重问同一道题"。与 01/03/04 可并行；无重复的回归 scorer 落在 05（依赖本条）。

## Parent

[PRD: M8 Eval Harness + 它护住的 dogfood 质量修复](../PRD.md)

## What to build

给考核循环加一份**代码持有、锚定 item_id 的会话内"已问过"台账**，保证复考同一薄弱概念时每轮是**不同角度**的题——体现"LLM 判卷，代码记账"。

- 台账 MVP 为进程内 `dict[item_id -> list[question]]`，在考核循环入口持有；打 `# SKELETON` 标记并在走骨架台账新增一行（正式版为与 Learning Memory 并列的 SQLite 去重表，跨会话持久，留后）。
- 台账经循环入口 → assess_once → 出题函数下传；出题时把已问过的题注入 user message 作为"请换一个角度提问"约束。
- 出题结构化输出门（`_parse` / `_parse_mc`）加**归一化去重校验**：新题归一化后命中已问过集合即 raise ModelRetry，复用现有有界重试与缝-3 校验门模式。
- **不在 selection 修**：排除刚问的 item 会破坏薄弱优先复考。修只在出题侧。

## Acceptance criteria

- [ ] 复考同一薄弱 item 连续多轮产出不同角度的题：缝-1 断言会话内 QUESTION_ASKED 归一化后零逐字重复（修前红、修后绿）
- [ ] 出题门对归一化后重复的题 raise ModelRetry 并走有界重试（缝-3）
- [ ] 已问过台账的归一化 / 命中判定有缝-2 单测
- [ ] 台账临时 dict 实现打 `# SKELETON` 标记；走骨架台账新增对应行，`grep -rn SKELETON src/` 数与台账未完成行数对齐
- [ ] selection 未被改动；薄弱优先复考回归（现有 case 5）仍绿
- [ ] 四门全绿

## Blocked by

None - can start immediately
