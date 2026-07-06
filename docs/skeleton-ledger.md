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
| 1 | Learning Memory | 进程内 dict（薄弱概念 + 三态 + 连对计数 + 判决历史） | SQLite 支持的 Memory 抽象（复用纯函数状态机 `apply_verdict`） | **M7** | 跨会话薄弱点持久，重启后仍薄弱优先出题 | ✅ `memory.py` 的 `SqliteLearningMemory`（`Memory` 协议 + `LearningMemory` dict 内存实现并存） |
| 2 | KnowledgeItem / Resource 存储 | 进程内 dict | SQLite（复用 kernel 参数化 `migrate` + `domain/learning/migrations/0001_learning.sql`） | **M7** | 入库 item 重启后仍在、仍可锚定出题 | ✅ `store.py` 的 `SqliteLearningStore`（`Store` 协议 + `LearningStore` dict 内存实现并存） |
| 3 | 审批门 | M3.1 已落 `ApprovalGate` 协议 + `ScriptedApprovalGate`（发 `approval.requested` 事件 + 脚本化决策）；CLI 阻塞 `prompt` 交互实现仍是后续 human 步骤 | 可挂起 / 可恢复 turn：凭 token 从待决状态恢复，跨 SSE / HTTP | **TBD**（随 `interfaces/api` 或专门加固；**接口形状第一天就按 suspend/resume 定**，故替换不改调用方） | 关掉 CLI 重开、凭 token 恢复同一次待审批会话 | ⬜ |
| 4 | Reader subagent 执行器 | M3.1 内联调用（隔离上下文 + pydantic 校验 + ModelRetry 已是真的） | `kernel/subagent.py` 通用执行器 | 出现**第二个** subagent 时再抽（无独立 M，YAGNI） | 第二个 subagent 复用同一执行器、零重复 | ⬜ |
| 5 | prompt 版本号 | ~~`MODEL_STARTED` 里手填 `prompt_version`~~ | prompt 模板独立存放（`prompts/*.md`）+ 内容 hash 版本号，trace 记版本号 | **✅ 已完成** | trace 能按 prompt 版本归因 eval 回归 | ✅ `domain/learning/prompts.py` + `prompts/reader_extract.md`（版本=内容 hash，Reader 加载） |
| 6 | Responder（作答输入原语） | M3.2 落 `Responder` 协议 + `ScriptedResponder`；交互 CLI 落地后已加 `InteractiveResponder`（questionary 逐题问，见下节）——**交互形态已到**，仍缺"可挂起 / 可恢复"（凭 token 续答）一段 | 可挂起 / 可恢复的作答 turn（凭 token 恢复，跨 SSE / HTTP，与审批门同形） | **TBD**（随 `interfaces/api` 加固；**接口形状第一天按 `Responder` 协议定**，替换不改 `assess_once` 调用方） | ~~CLI 里逐题作答~~（✅ 已达）；关掉重开可凭 token 续答（仍缺） | ⬜ |

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

## M7 存储 / Learning Memory 正式化为 SQLite 落地

M7 把 **#1 Learning Memory** 与 **#2 KnowledgeItem / Resource 存储** 从进程内 dict 假件正式化为
SQLite 持久化，兑现两条验收信号（跨会话薄弱点持久 / 入库 item 重启后仍在）。落地要点：

- **抽协议、dict 降格为内存实现**：`store.py` 定义 `Store` 协议、`memory.py` 定义 `Memory` 协议；
  原 dict 类（`LearningStore` / `LearningMemory`）保留原名、语义改为"测试 / 快速用的**内存实现**"
  （不再是骨架欠账）。`ingest.py` / `assessment.py` / `selection.py` 的 store/memory 形参类型改为协议，
  故 dict 版与 SQLite 版都满足、**调用方一行逻辑不改**即可替换（兑现"替换不改调用方"）。
- **kernel `migrate` 参数化为通用 runner**：`kernel/db.py` 的 `migrate(conn, migrations_dir=…)`
  默认仍走 `kernel/migrations`（`TraceStore` 调 `migrate(conn)` 不变、向后兼容），domain 传入
  `domain/learning/migrations` 复用同一 runner——kernel 仍不认识任何领域表。learning 数据用**独立
  db 文件**（与 trace.db 分开），各自 `PRAGMA user_version` 与迁移序列。
- **schema 无时间戳列**（决策 2）；list 字段（evidence / verdict_history）存 JSON 文本；`trusted` 存
  0/1；销账 = `DELETE` 行；`SqliteLearningMemory` 复用纯函数 `apply_verdict`（状态机不重写），脏行经
  `ConceptRecord.model_validate` 被 M3.3 的不变量 validator 兜底。

**grep 对账（M7 后）**：删除 `store.py` / `memory.py` 的 `# SKELETON(M7)` 标记后，
`grep -rn "SKELETON" src/` 应为 **3** 处（#3 approval / #4 reader / #6 responder），与台账未完成行数
**3** 一致——#1 / #2 已 ✅ 结清，两边对齐。

## 交互 CLI 落地（#6 Responder 交互形态到位，suspend/resume 仍留后续）

交互 CLI 把 **#6 Responder** 从"只有确定性 `ScriptedResponder`"推进到"交互形态已到"：

- `Responder` 协议改 **async + 加 `options`**：`async def answer(self, prompt, *, options=None) -> str`。
  `assess_once` 改 `await responder.answer(question_text, options=…)`——选择题透传 `mc.options`、
  开放 / 追问传 `None`。`ScriptedResponder` 同步改 async（忽略 `options`），既有调用方（全经
  `assess_once`）透明，无一处直接 `.answer()` 调用需改。
- 新增 `interfaces/cli/InteractiveResponder`（questionary：`options` 非空 → `select` 单选，否则
  `text` 自由输入；均用 `.ask_async()`，取消 → `KeyboardInterrupt` 由 quiz 命令捕获优雅退出）。
- 新增 argparse 子命令路由（`interfaces/cli/app.py`，`grandquiz` 脚本入口指向它）：`ingest`
  （读本地材料 → 真 Reader 深读 → keep-all 审批 → 入 SQLite）与 `quiz`（逐题交互考核，持久 SQLite，
  薄弱点跨会话留存）。`QuizEventPrinter` 订阅事件流做 Rich 呈现——**CLI 是事件脊柱的消费者**。

**仍留后续（故 #6 状态保持 ⬜、SKELETON 标记保留）**：可挂起 / 可恢复的作答 turn（凭 token 续答、
跨 SSE / HTTP），随 `interfaces/api` 加固；接口形状已按 `Responder` 协议焊死，替换不改 `assess_once`。
真机交互 tty 试跑（`grandquiz quiz` 逐题手答）属 human 步骤，不在 CI 内跑。

**grep 对账（交互 CLI 后）**：`grep -rn "SKELETON" src/` 仍为 **3** 处（#3 approval / #4 reader /
#6 responder），与台账未完成行数 **3** 一致——#6 交互形态到位但 suspend/resume 未了，标记不撤。

## 变更约定

- 每个引入 / 消除骨架欠账的 PR，**必须同步改本表**（加行 / 改状态 / 删代码标记），与 issue 一一对应。
- 替换某行时，其"验收信号"列即该 PR 的验收标准之一。
