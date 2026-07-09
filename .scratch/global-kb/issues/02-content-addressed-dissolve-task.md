# GKB-S2 — 内容寻址资源 + 消解 LearningTask + `resources.topic` 列（清终态）

Status: ready-for-agent
Type: AFK

## Parent
[PRD: 全局 KB 重构](../PRD.md)

## What to build

把知识模型收敛到全局 KB 的干净终态：资源**内容寻址**（`resource_id = derive_id(url)`、同 URL 全局唯一），
**消解 `LearningTask` 实体**（`tasks` 表弃用），`resources` 加可空 `topic` 软标签列（本 slice 先建列、留空，
S3 填），出题/判卷 `language` 归入 Preference Memory。清库重来（不迁移旧数据）。这是全局 KB 的重塑基座——较重
但原子。

## 锁定设计（不留给实现猜）

- **内容寻址**：`LearningResource.create(url)` → `resource_id = derive_id(url)`（去掉 `task_id` 入参）。
  `LearningResource` 去 `task_id` 字段、加 `topic: str | None = None`。`item_id = f"{resource_id}#{index:03d}"`
  资源内唯一**不变**（ADR-0002 边界不动，`concept_key` 二期缝保留）。同 URL 重 ingest → 同 resource_id →
  INSERT OR REPLACE 天然去重。
- **消解 `LearningTask`**：删除 `LearningTask` 类及 `tasks` 表相关读写。级联清理签名：
  - `ingest_resource(url, …)`：去 `task` 入参、去 `store.add_task`；资源用 `derive_id(url)` 建。
  - `assess_once(…)`：去 `task` 入参。事件 `ASSESSMENT_STARTED` payload 原 `task_id` **换判别力字段**
    （候选池大小；scope 字段留给 S4）。语言解析见下。
  - `register_learning_tools` / `make_ingest_tool` / `make_query_weak_concepts_tool` / `make_start_quiz_tool`：
    去 `task` 线程。
  - `Store` 协议：去 `add_task` / `get_task` / `items_for_task`（全局 KB 无 task 分区；`all_items()` 已是唯一读）。
- **语言归 Preference**：`_resolve_language` 变 **偏好(`question_language`) > 硬兜底"中文"**（去掉 task.language 一支）。
  `PreferenceMemory` 不变（已有 `question_language`）。
- **schema（清库、直接落新形状）**：新增顺序迁移 SQL（bump user_version）——`resources` 无 `task_id`、有 `topic`；
  弃 `tasks` 表。**不写数据迁移/回灌**（旧库归档，用户重新 ingest）。dict 版同步反映新形状。
- **CLI**：`run_react` / `run_quiz` / `_run_quiz_cli` 不再建 `LearningTask`；`title` 位置参数降为**可选横幅**
  （只用于打印，不进任何派生/分区）。
- **ADR + CONTEXT.md**：出 ADR「全局 KB / LearningTask 消解」（记 `resource_id=derive_id(url)`、task→无实体、
  跨 task 薄弱互见为 ADR-0003 期望）；改写 CONTEXT.md `LearningTask` 词条；更新 `query_weak_concepts` 原
  "跨任务隔离" 注释（现全局互见）。
- **确定性/replay**：`derive_id(url)` 仍是稳定 hash；单资源 eval 夹具下资源内 index 序不变 → 选题不变 →
  message 内容基 → `replay_key` 保留 → golden cassette 应零漂移（**建后先核 replay 绿**；真漂移则重录）。
- **分层**：全改在 domain/learning + interfaces/cli + evals；kernel 无感（lint-imports 绿）。

## Acceptance criteria

- [ ] `resource_id = derive_id(url)`；`LearningResource` 去 task_id、加 `topic`（默认 None）
- [ ] `LearningTask` 类与 `tasks` 表弃用；`ingest_resource`/`assess_once`/工具工厂/`register_learning_tools` 去 task 线程
- [ ] 语言解析改 **偏好 > 中文**；`Store` 去 task 相关方法、`all_items()` 为唯一读
- [ ] 新迁移 SQL 落新 `resources` 形状（无 task_id/有 topic）、弃 tasks 表；dict 版同步；migrate 幂等
- [ ] CLI title 降为可选横幅；`run_react`/`run_quiz` 不建 LearningTask
- [ ] **同 URL 去重验收**：不同会话 ingest 同一 URL → 单一 resource（INSERT OR REPLACE）
- [ ] `ASSESSMENT_STARTED` payload 去恒定 task_id、带候选池大小；`ASSESSMENT_REFUSED(empty_kb)` 语义保留（case2 绿）
- [ ] ADR 落 `docs/adr/`；CONTEXT.md `LearningTask` 词条改写；相关注释更新
- [ ] 既有 eval + golden cassette 绿（单资源夹具选题不漂）；若 replay 漂移，真机重录 `assess.cassette.json`
- [ ] TDD：resource_id 派生、topic 列读写、语言解析优先级、迁移幂等、dict/SQLite parity，各 mutation 可杀
- [ ] 五门全绿（含 lint-imports）

## Files (owner, 可能漂)
`domain/learning/models.py`、`domain/learning/store.py`(+迁移 SQL)、`domain/learning/ingest.py`、
`domain/learning/assessment.py`、`domain/learning/tools.py`、`domain/learning/preference.py`(如需)、
`interfaces/cli/app.py`、`evals/harness.py`、`docs/adr/000X-*.md`、`CONTEXT.md`、相关测试。

## Blocked by
GKB-S1（`all_items()` 全局读须先在，本 slice 才能安全弃 `items_for_task`）。
