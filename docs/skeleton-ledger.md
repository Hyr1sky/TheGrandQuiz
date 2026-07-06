# 走骨架替换台账（Walking-Skeleton Ledger）

> 竖切原则（见 [architecture.md](architecture.md) 搭建顺序、[CLAUDE.md](../CLAUDE.md) 开发节奏）：
> M3 考核竖切**先穿透**——用最小假实现让整条链路亮起来，kernel 各层再由真实 domain
> 拉动着逐层加硬。本台账钉死每一处"临时假实现"的正式版本、替换里程碑与验收信号，
> **防止竖切跑通后遗忘补齐**（"跑通 ≠ 做完"）。

## 两条纪律

1. **代码里打标记**：每处临时假实现旁写一行
   `# SKELETON(Mx): <一句话> — 见 docs/skeleton-ledger.md`，
   于是 `grep -rn "SKELETON" src/` 能一键枚举全部欠账（机读视图）。
2. **本表是人读视图**：新增假实现时同步加一行；替换完成后把状态改 ✅ 并删掉对应代码标记。
   代码里 `grep` 到的 `SKELETON` 数应与本表未完成行数一致——两边对不上就说明有人偷偷加了假实现没记账。

**区分骨架欠账与范围边界**：本表只收**骨架欠账**（为让竖切早点亮而临时假的、我们一定会补的实现）。
PRD 里的 **Out of Scope**（资源自动发现 / 向量库 / Web 前端 / 跨资源归并等 MVP 刻意不做、未必会做的）
是**范围边界**，不进本表。

## 台账

| # | 组件 / 缝 | M3 临时实现（fake） | 正式实现 | 替换里程碑 | 验收信号 | 状态 |
|---|---|---|---|---|---|---|
| 1 | Learning Memory | 进程内 dict（薄弱概念 + 三态 + 连对计数 + 判决历史） | SQLite 支持的 Memory 抽象（store / recall / policy） | **M7** | 跨会话薄弱点持久，重启后仍薄弱优先出题 | ⬜ |
| 2 | KnowledgeItem / Resource 存储 | 进程内 dict | SQLite（复用 M2 的迁移机制，加 `000N_learning.sql`） | **M7** | 入库 item 重启后仍在、仍可锚定出题 | ⬜ |
| 3 | 审批门 | M3.1 已落 `ApprovalGate` 协议 + `ScriptedApprovalGate`（发 `approval.requested` 事件 + 脚本化决策）；CLI 阻塞 `prompt` 交互实现仍是后续 human 步骤 | 可挂起 / 可恢复 turn：凭 token 从待决状态恢复，跨 SSE / HTTP | **TBD**（随 `interfaces/api` 或专门加固；**接口形状第一天就按 suspend/resume 定**，故替换不改调用方） | 关掉 CLI 重开、凭 token 恢复同一次待审批会话 | ⬜ |
| 4 | Reader subagent 执行器 | M3.1 内联调用（隔离上下文 + pydantic 校验 + ModelRetry 已是真的） | `kernel/subagent.py` 通用执行器 | 出现**第二个** subagent 时再抽（无独立 M，YAGNI） | 第二个 subagent 复用同一执行器、零重复 | ⬜ |
| 5 | prompt 版本号 | ~~`MODEL_STARTED` 里手填 `prompt_version`~~ | prompt 模板独立存放（`prompts/*.md`）+ 内容 hash 版本号，trace 记版本号 | **✅ 已完成** | trace 能按 prompt 版本归因 eval 回归 | ✅ `domain/learning/prompts.py` + `prompts/reader_extract.md`（版本=内容 hash，Reader 加载） |
| 6 | Responder（作答输入原语） | M3.2 已落 `Responder` 协议 + `ScriptedResponder`（注入固定 / 按序答案，确定性）；交互式 CLI Responder（阻塞 `prompt`）仍是后续 human 步骤 | 交互式 / 可挂起-恢复的作答 turn（凭 token 恢复，跨 SSE / HTTP，与审批门同形） | **TBD**（随 `interfaces/cli` 或 `interfaces/api`；**接口形状第一天按 `Responder` 协议定**，替换不改 `assess_once` 调用方） | CLI 里逐题作答；关掉重开可凭 token 续答 | ⬜ |

其余 kernel 层（HookManager 异常隔离→M4、ContextBuilder→M5、RecoveryPolicy→M6、Eval harness→M8）
不是"假实现"而是"尚未上线的层"，其排期见 [roadmap.md](roadmap.md) 增量路线，不在本表重复。

## M3.1 ingest 竖切落地的骨架标记

M3.1（喂 URL → 深读 → 审批 → 入库）落地了下列骨架欠账的**假件**（状态仍 ⬜，正式实现见各行里程碑）：

| 台账行 | 代码标记 | 位置 |
|---|---|---|
| #2 存储 | `# SKELETON(M7)` | `src/grandquiz/domain/learning/store.py`（`LearningStore` 纯 dict） |
| #3 审批门 | `# SKELETON` | `src/grandquiz/domain/learning/approval.py`（`ApprovalGate` 协议 + `ScriptedApprovalGate`） |
| #4 Reader 执行器 | `# SKELETON` | `src/grandquiz/domain/learning/reader.py`（`Reader` 内联执行器） |

（#5 prompt 版本号已在 item 2 落地为版本化 prompt 文件，代码标记随之移除。）

**grep 对账**：`grep -rn "SKELETON" src/` 现有 **3** 处标记（上表 #2/#3/#4）。台账未完成行为 4（#1~#4），
差的一处是 **#1 Learning Memory**——它属考核循环后半段（选题 / 判卷 / 销账），M3.1 ingest 竖切**不触及**，
其 dict 假件将在 M3.2+ 引入时补上代码标记。届时 grep 数应回到与未完成行数一致。

## M3.2 单题考核竖切落地的骨架标记

M3.2（考我 → 选题 → 出题 → 答 → 判卷）新增一处骨架欠账的**假件**（状态 ⬜，正式实现见里程碑）：

| 台账行 | 代码标记 | 位置 |
|---|---|---|
| #6 Responder | `# SKELETON` | `src/grandquiz/domain/learning/responder.py`（`Responder` 协议 + `ScriptedResponder`） |

M3.2 **不引入** #1 Learning Memory 的 dict 假件——单题竖切只发 `ANSWER_JUDGED`（含 `verdict` +
代码算出的 `weak_item_id`），**不写任何记忆库**；薄弱状态机 / 三态 / 连对销账 / 薄弱优先选题是
**M3.3** 的活（届时 `selection.select_target` 换内部实现、`assess_once` 消费判决落库，签名不变）。

**grep 对账（M3.2 后）**：`grep -rn "SKELETON" src/` 应为 **4** 处（#2 store / #3 approval / #4 reader /
#6 responder）。台账未完成行为 **5**（#1~#4、#6），差的一处仍是 **#1 Learning Memory**——M3.2 未触及，
其 dict 假件将在 M3.3 引入选题 / 销账时补上代码标记。届时 grep 数应回到与未完成行数一致。

## M3.3 薄弱记忆 + 三态状态机落地的骨架标记

M3.3（判卷后代码记账：三态状态机 + 连对销账 + 薄弱优先复考）引入 **#1 Learning Memory** 的
dict 假件（状态仍 ⬜，正式 SQLite 实现见里程碑 **M7**）：

| 台账行 | 代码标记 | 位置 |
|---|---|---|
| #1 Learning Memory | `# SKELETON(M7)` | `src/grandquiz/domain/learning/memory.py`（`LearningMemory` 纯 dict：锚定 `item_id` 存 三态 + 连对计数 + 判决历史；`apply_verdict` 是纯函数状态机） |

至此考核循环的后半段（选题 / 判卷 / 销账）在事件脊柱上打通：`selection.select_target` 接
`memory` 走薄弱优先候选集（签名向后兼容，`memory=None` 退化全集），`assess_once` 判卷后由代码
调 `memory.record_verdict` 记三态账并发 `learning.concept_state_changed`。dict 仍是假件——
跨会话持久（重启后仍薄弱优先出题）留给 **M7** 用 SQLite 支持的 Memory 抽象替换，届时调用方签名不变。

**grep 对账（M3.3 后）**：`grep -rn "SKELETON" src/` 应为 **5** 处（#1 memory / #2 store /
#3 approval / #4 reader / #6 responder），与台账未完成行数 **5**（#1~#4、#6）一致——欠账已全部记账，
两边对齐。

## 变更约定

- 每个引入 / 消除骨架欠账的 PR，**必须同步改本表**（加行 / 改状态 / 删代码标记），与 issue 一一对应。
- 替换某行时，其"验收信号"列即该 PR 的验收标准之一。
