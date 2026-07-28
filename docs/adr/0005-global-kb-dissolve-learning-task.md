# ADR-0005: 全局知识库 / LearningTask 消解

- 状态：已接受
- 日期：2026-07-09

## 背景

第五轮真机 dogfood 抓到两个同根问题：

- **换标题就"丢"知识**：两次会话用了不同启动标题（`react "Hook 详解"` vs `react "代理通信协议"`），
  上一次 ingest 的知识在这次"消失"了。根因不是持久化 bug，而是**范围模型**——知识按
  `task_id = derive_id(title)` 分库，换标题即换库，把持久全局库切成了互不相通的孤岛。
- 用户心智里这是**一个持久的个人学习库**，想用自然语言在里面切换材料问答，而不是"一个启动标题
  锁死一份文件"。

原模型（`LearningTask` 作为容器与考核范围，资源 / KnowledgeItem 挂其下，`resource_id =
derive_id(task_id, url)`）把"会话"和"知识范围"绑死在一个标题上，与心智不符，也让跨会话复习变得
不可能。用户确认历史 dogfood 数据不重要，可清库重来。

## 决策

把知识模型收敛到**全局 KB 干净终态**，消解 `LearningTask` 实体：

- **内容寻址**：`resource_id = derive_id(url)`（去掉 `task_id` 入参）。同 URL 全局唯一 →
  `INSERT OR REPLACE` 天然去重（同一 URL 在不同会话重 ingest 只产生一个 resource）。
  `item_id = f"{resource_id}#{index:03d}"` 资源内唯一**不变**（ADR-0002 边界不动，`concept_key`
  二期缝保留）。
- **`LearningResource`**：去 `task_id` 字段、加可空 `topic: str | None`（资源级软标签，"这份材料
  讲什么"，本 slice 先建列留空，S3 由 Reader 抽）。
- **`LearningTask` 类与 `tasks` 表弃用**。级联：`ingest_resource` / `assess_once` / 工具工厂
  (`make_ingest_tool` / `make_query_weak_concepts_tool` / `make_start_quiz_tool`) /
  `register_learning_tools` 去 task 线程；`Store` 协议去 `add_task` / `get_task` /
  `items_for_task`（`all_items()` 是唯一的全局选题读，GKB-S1 已加）。
- **语言归 Preference Memory**：出题 / 判卷语言从 task 属性移出，`_resolve_language` 变
  **偏好(`question_language`) > 硬兜底"中文"**（去掉 `task.language` 一支）。语言是跨全库的个人
  设置，不是材料 / 任务属性。
- **schema**：新增顺序迁移（`0004_global_kb.sql`，bump `user_version` 到 4）——直接落新
  `resources` 形状（无 `task_id`、有 `topic`）、`DROP TABLE tasks`。**不写数据迁移 / 回灌**（旧库
  归档、用户重新 ingest）；migrate 幂等（靠 `user_version` 只跑一次）。dict 版 `LearningStore` 同步
  反映新形状。
- **CLI**：`run_react` / `run_quiz` / `_run_quiz_cli` 不再建 `LearningTask`；`title` 位置参数降为
  **可选横幅**（只打印开场白，不进任何派生 / 分区）。
- **事件脊柱**：`ASSESSMENT_STARTED` payload 的恒定 `task_id` 判别力字段退役，换成**候选池大小**
  (`candidate_pool_size`)；`ASSESSMENT_REFUSED(empty_kb)` payload 去 `task_id`（语义保留）。

**跨 task 薄弱互见**是 ADR-0003（记忆四收二）落地的期望终态：薄弱概念锚定 KnowledgeItem，本就
不该按 task 分区；全局 KB 让"换会话仍能复习此前学的"成为默认行为，`query_weak_concepts` /
学情注入 / 薄弱小结全部走全库读。

## 备选方案

- **保留 `LearningTask`、只放宽选题候选池到全库**（GKB-S1 的过渡态）：修了"换标题丢知识"（#2），
  但仍留着一个不再承载范围语义的空实体、`tasks` 表、`resource_id` 里的 `task_id` 分量，是长期
  技术债。既然清库重来，一步到干净终态。
- **写数据迁移把旧 `tasks` / 老 `resource_id` 回灌到新形状**：用户确认历史 dogfood 数据不重要，
  回灌是纯负担、且老 `resource_id = derive_id(task_id, url)` 与新 `derive_id(url)` 不同、无法
  无损平移。清库重来更简单可靠。
- **语言留在 per-material（`resources.language`）**：语言是学习者的个人偏好、跨材料一致，挂在
  资源上会让"我想全程用英文考"变成逐材料设置。归 Preference Memory 更贴心智；日后真要按材料区分
  再加列（Out of Scope）。

## 后果

**好处**：知识模型与"一个持久全局 KB"的用户心智对齐；换会话不丢知识；`resource_id` 内容寻址对
将来的远程 URL 抓取前向兼容（`https://…` 走同一派生、模型零改动）；`topic` 列为 S3 目录式 scope
（PRD 分叉2）铺好地基。

**代价 / 风险**：一次性清库（旧 dogfood 数据丢弃）；这是一次较重但原子的重塑，所有引用
`LearningTask` / `items_for_task` / `add_task` 的代码与测试都同步更新到新形状。

**确定性 / replay**：`derive_id(url)` 仍是稳定 hash；单资源夹具下资源内 index 序不变 → 选题选中的
item 内容不变 → 出题 / 判卷 message 基不变 → golden cassette 逐字节保留（无需重录）。

**重新审视信号**：若日后需要"命名的学习计划 / 课程"这类真正的范围实体（而非启动标题），或需要
per-material 语言 / 难度，再引入新的分区维度——但那应是查询期的软 scope（PRD 分叉2 目录式），
不是回到"标题锁库"。
