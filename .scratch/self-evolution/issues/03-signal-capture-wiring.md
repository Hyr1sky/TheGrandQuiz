# SE-S3 — 信号采集 + 记账接线 + 透明展示事件（把 S1+S2 接进考核编排）

Status: ready-for-agent
Type: AFK

## Parent
[PRD: 自进化第一阶段](../PRD.md)

## What to build

在 `assess_once` 里**采集三路信号 → 调 S2 规则 → 写 S1 台账 → 真跨档时发 `DIFFICULTY_TIER_CHANGED`
事件**。这一步把前两个增量接进真实考核编排——完成后系统**已经会自进化 + 透明展示**（难度会变、会
在 CLI 显示），即使还没影响出题（那是 S5/S6）。这是本 PRD 第一条**用户可感知的竖切**。

## 锁定设计（不留给实现猜）

- **销账轮数捕获**（PRD 决策 3 信号 1）：`apply_verdict` 返回 `None`（销账）的那一刻，被删除的
  `ConceptRecord.verdict_history` 长度即销账轮数。**必须在此刻捕获**——记录随即被删、事后无法
  回读。接线点在 `assess_once` 记账后、发 `CONCEPT_STATE_CHANGED` 附近（那里已能拿到转移前的
  record）。注意：`apply_verdict` 拿到的 `record` 是转移**前**的快照，其 `verdict_history` 需加上
  本次判决才是完整轮数——实现时确认口径、写清。
- **耗时近似捕获**（信号 2）：从本轮 `QUESTION_ASKED` 与 `ANSWER_JUDGED` 的事件时间戳取差值。
  replay 下时间戳由 `ManualClock` 提供（确定）。拿不到（如某些路径无时间戳）→ 传 `None`，规则容忍。
  **零新埋点**——只读既有事件已有的时间戳。
- **判决分布信号**（信号 3）：从同一份 `verdict_history` 派生（是否掉过"勉强"等），喂 S2。
- **调 S2 + 写 S1**：`current = ledger.tier_of(item_id)` → `new = next_tier(current, signals)` →
  `if new != current: ledger.set_tier(item_id, new)` + 发事件。
- **难度自适应的触发时机**：**本期只在"销账"这一确定时刻更新难度**（信号 1 只在那时可得）。非销账
  轮不动难度（简化 v1；"每轮都微调"留后续）。——此约束须在 `assess_once` docstring 写清。
- **`DIFFICULTY_TIER_CHANGED` 事件**（PRD 决策 6）：
  - `LearningEvent` 加常量 `DIFFICULTY_TIER_CHANGED = "learning.difficulty_tier_changed"`。
  - **仅真跨档时发**（`new != current`）；payload：`item_id / concept / from_tier / to_tier /
    reason`（reason 为据哪路信号跨档的简短说明，取自 S2 规则的可解释性）。
  - 发射位置：照 `CONCEPT_STATE_CHANGED` 先例，在 `assess_once` 内经传入的 `emitter.emit`，
    parent 挂 assessment span；`ANSWER_JUDGED` / `CONCEPT_STATE_CHANGED` 之后。
- **依赖注入**：`assess_once` 新增 `difficulty: DifficultyLedger | None = None` 可选形参
  （`None` = 不接难度自适应，**行为逐字节等价于改动前**，向后兼容——同 `asked_questions` 的接法）。
  `start_quiz` 工具 / `run_quiz` / `composition.build_learning_stores`（→ 现 4 元组扩 5 元组）/
  `commands/react.py` / `commands/quiz.py` 逐层透传 + `finally` 里 `close()`。
- **`QuizEventPrinter`**：订阅渲染 `DIFFICULTY_TIER_CHANGED`（"『概念』难度：3 → 4，因……"）。

## Acceptance criteria

- [ ] `assess_once` 在销账时采集三路信号、调 `next_tier`、真跨档才写台账 + 发事件
- [ ] `LearningEvent.DIFFICULTY_TIER_CHANGED` 常量 + payload 含 from/to/reason
- [ ] **仅真跨档才发**事件（档位不变时不发）——有测试钉死
- [ ] 销账轮数口径正确（转移前 history + 本次判决），有测试覆盖"快速销账升档""拖沓销账不升"
- [ ] 耗时缺失路径不崩（传 None、规则容忍）
- [ ] `difficulty=None` 缺省路径**逐字节等价改动前**：既有 assess/react eval + golden cassette 全绿
- [ ] `build_learning_stores` 扩为 5 元组，两条 CLI 命令透传 + `finally` close
- [ ] `QuizEventPrinter` 渲染新事件（照现有事件渲染测法）
- [ ] 五门 + eval harness 全绿

## Files (owner, 可能漂)
`domain/learning/assessment/engine.py`(采集 + 接线 + 发事件)、`domain/learning/events.py`(新常量)、
`domain/learning/tools/start_quiz_tool.py`(透传 difficulty)、`domain/learning/tools/__init__.py`
(register 透传)、`interfaces/cli/composition.py`(build_learning_stores 5 元组 + build_react_runner)、
`interfaces/cli/commands/{react,quiz}.py`(解包 + close)、`interfaces/cli/printer.py`(渲染)、
`tests/test_assessment.py` 等。

## Blocked by
SE-S1（台账）、SE-S2（规则）。
