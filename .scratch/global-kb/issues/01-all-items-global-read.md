# GKB-S1 — `all_items()` 全局读 + 切读（修 #2 跨会话丢知识）

Status: done（merge 至 main `c7ac2f5`，五门全绿 418 passed；串行对抗验证 6 mutation 全杀、不 flaky、cassette 逐字节保留。额外一致切了 quiz CLI 路径两处同族读点；test_react_tools 一处"跨任务隔离"用例按全局 KB 翻转。）
Type: AFK

## Parent
[PRD: 全局 KB 重构](../PRD.md)

## What to build

给知识存储加一个 **canonical 全局读访问器 `all_items()`**（返回全库所有 KnowledgeItem，不按 task 分区），
并把当前所有"按 task 读 item"的调用点切过去。这是**最小、零派生改动**的第一竖切：不碰 `derive_id`、不碰
schema、不删 `tasks` 表——仅新增一个读方法 + 切读，就把 **#2 跨会话丢知识** 修掉（换启动标题开新会话也能考到
之前 ingest 的知识）。派生/消息不变 → golden cassette 逐字节保留。

## 锁定设计（不留给实现猜）

- **`Store` 协议 + 两实现新增 `all_items()`**：
  - 语义：返回**全库**所有 KnowledgeItem，**按 item_id 升序**（与 `items_for_task` 同一顺序契约——选题
    `select_target` 用 `rng.choice` 按下标选，两实现顺序不一致则同种子选不同 item、replay 不对齐）。
  - `SqliteLearningStore`：`SELECT … FROM knowledge_items ORDER BY item_id`（**不 join resources、不按
    task_id 过滤**）。
  - dict `LearningStore`：`sorted(self._items.values(), key=lambda i: i.item_id)`。
  - **保留** `items_for_task`（本 slice 不删；S2 才可能改/弃）。
- **切读**：把下列"按 task 读全量 item"的点全部改调 `all_items()`（这些点原本是"本 task 的池"，全局 KB 下
  应是"全库的池"）：
  - `assess_once` 取候选池（原 `store.items_for_task(task.task_id)`）。
  - `query_weak_concepts` 工具 handler 的 `concept_by_id`。
  - `_weak_concepts`（薄弱概念摘要）。
  - `start_quiz` 工具 handler 的 `concept_by_id`。
  - `render_learner_context` / `_render_weak` 的 `concept_by_id`。
  - **eval harness 的 natural 对照基线**（必须与生产同源，否则 case5/case10 对错误基线打分）。
  - `_run_quiz_cli` 的空库预检（原按 title 派生 task_id 查 items_for_task）。
- **task 仍在**：本 slice 不消解 `LearningTask`（那是 S2）。`assess_once` 等仍收 `task`（用于事件 task_id、
  经 `task.language` 解析语言）——只是选题的**候选池**从 task 局部改成全库。
- **确定性**：纯读方法、无 clock/random/time；两实现同序契约是 replay 命门。

## Acceptance criteria

- [ ] `Store` 协议 + `LearningStore`(dict) + `SqliteLearningStore` 均实现 `all_items()`，返回全库 item、按 item_id 升序
- [ ] **dict/SQLite parity 测试**：多个不同 hash 前缀 resource_id 下的 item，两实现 `all_items()` 序列逐条相等（跨资源、稳定升序）
- [ ] 上列所有读点切到 `all_items()`；`items_for_task` 保留不动
- [ ] **跨会话召回验收**（修 #2）：以标题 A ingest 知识后，用**不同标题 B** 开会话，`start_quiz` 能考到 A 的 item（集成测试或 CLI replay 断言）
- [ ] 既有 8+2 eval 用例 + golden cassette (`assess.cassette.json`) 逐字节绿（单资源夹具下选题不变、replay 不漂）
- [ ] TDD：`all_items` 空库/单资源/多资源、升序、parity，各 mutation 可杀
- [ ] 五门全绿（含 lint-imports）；`derive_id`/models/schema/cassette 空 diff

## Files (owner, 可能漂)
`domain/learning/store.py`(Store 协议 + 两实现 + all_items)、`domain/learning/assessment.py`(切读)、
`domain/learning/tools.py`(切读×2)、`domain/learning/context.py`(切读)、`evals/harness.py`(natural 基线切读)、
`interfaces/cli/app.py`(_run_quiz_cli 空库预检)、`tests/test_sqlite_store.py`(parity)、相关 assessment/tools/cli 测试。

## Blocked by
None（基线 main `8450499`；task 模型不动）。
