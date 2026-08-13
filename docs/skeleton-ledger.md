# 走骨架替换台账

本表只记录为了尽早穿透竖切而保留的临时实现。范围外功能、未获准实验和普通 backlog 不进入本表。

## 纪律

1. 临时实现旁必须带 `# SKELETON` 标记并引用本表行号。
2. 正式替换后删除代码标记，并把对应行改为完成。
3. `rg -n "SKELETON" src/` 的当前标记数必须与未完成行数一致。
4. 历史阶段计数与落地过程由 Git 和 [devrecords](devrecords/) 保存，不在当前台账重复。

## 当前台账

| # | 组件 / seam | 正式目标 | 进入条件 / 里程碑 | 状态 |
|---|---|---|---|---|
| 1 | Learning Memory | SQLite Memory 抽象与跨会话薄弱状态 | M7 | ✅ |
| 2 | KnowledgeItem / Resource 存储 | SQLite Store 与原子快照 | M7 | ✅ |
| 3 | 审批门 | 持久 `needs_input`、单次 token、跨 HTTP/SSE 恢复 | LW-S5 | ✅ |
| 4 | Reader subagent 执行器 | 通用 `kernel/subagent.py` 执行器 | 出现第二个真实 subagent 后再抽；零重复 | ⬜ |
| 5 | Prompt 版本 | 独立 prompt 文件、内容 hash、Trace 归因 | 稳定性加固 | ✅ |
| 6 | Responder | 可挂起/可恢复的作答 turn，凭 token 跨进程续答 | AssessmentSession 持久化需求明确时共同设计 | ⬜ |
| 7 | 考核轮次恢复 | `RecoveryPolicy + ErrorClass` 统一裁决 | M6 | ✅ |
| 8 | 已问题目去重 | SQLite 跨会话台账 | 2026-07-13 | ✅ |

## 当前对账

源码应只有两处标记：

- `domain/learning/ingest/reader.py`：#4 Reader 通用执行器；
- `domain/learning/responder.py`：#6 可恢复作答 turn。

内存 Store/Memory、Scripted Provider/Responder 等作为正式测试 Adapter 存在时，不属于骨架欠账。

## 变更约定

引入或结清骨架欠账的提交必须同时更新代码标记和本表。若标记数与未完成行数不同，文档门应失败。
