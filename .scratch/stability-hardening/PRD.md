# PRD：稳定性加固（文档权威基线 + P1/P2 完整性修复）

Status: HITL closing（2026-07-17：S1-S9 代码完成；待真实 DB、两份 cassette、CLI 真机验收）
Triage: ready-for-agent

## Problem Statement

TheGrandQuiz 已从考核竖切推进到可真机使用的 ReAct + 全局 KB + 自适应难度，但一次全仓库审计暴露出
几类会削弱“可观测、可恢复、可评测”叙事的完整性问题：

1. 资源与 KnowledgeItem 的身份在重 ingest 时不稳定，可能把旧的薄弱状态、已问题目和难度错绑到新概念。
2. 用户点名材料但 LLM 没解析出 `resource_id` 时，缺省 `None` 会退回全库，仍可能考错材料。
3. 网页大小限制发生在完整响应进内存之后，无法真正限制资源消耗。
4. Replay 指纹忽略工具契约，工具 schema / description 改动后 eval 可能继续命中旧 cassette 而假绿。
5. 多份领域账本独立提交、TraceStore 与展示 observer 共用失败语义、工具循环出站上下文不受总预算约束。
6. 自进化“一路答对也升档”和真实审批门在审计时未兑现；现已实现，仍需真机验收收口。

这些问题必须先于新一轮自进化能力扩展解决。否则新增状态会继续建立在不稳定的 KnowledgeItem 身份和
不完整的 Replay 证明上。

## Goal

建立一条可信的稳定性基线：领域身份稳定、重建语义明确、持久状态原子、外部 I/O 有真实资源上限、
Replay 能识别当前执行契约、trace 失败不会静默伪装成功，且所有权威文档与代码事实一致。

## User Stories

1. 作为用户，我 ingest 两个不同目录下的同名文件时，希望它们成为两个不同资源，不能静默覆盖。
2. 作为用户，我重新 ingest 同一资源时，希望 KB 只保留本次获批快照，不残留旧 KnowledgeItem。
3. 作为用户，我希望薄弱状态、已问题目和难度始终属于同一个概念，不能因 Reader 重排而串账。
4. 作为用户，我点名一个系统无法识别的材料时，希望系统诚实拒答，而不是改考全库其他材料。
5. 作为用户，我抓取异常大网页时，希望系统在下载途中停止，而不是完整读进内存后才报错。
6. 作为开发者，我修改工具契约后，希望旧 cassette 大声失效，eval 不得复用旧工具决策假绿。
7. 作为开发者，我希望一次答题产生的薄弱状态与难度变化要么一起提交，要么一起失败。
8. 作为开发者，我希望 TraceStore 写入失败成为可观察的 turn 失败，不能只写日志后继续成功。
9. 作为开发者，我希望发给 Provider 的完整请求都受预算约束，包括 tool specs、工具结果和持久题目历史。
10. 作为用户，即使一个概念从未答错，我连续答对后也希望系统逐步提升难度。
11. 作为用户，我希望 Reader 候选在入库前真的可被审批剔除，而不是生产路径自动 keep-all。
12. 作为维护者，我希望 README、PRD、ADR 与 skeleton ledger 对当前进度和剩余工作给出一致答案。

## Locked Decisions

### 数据处理

- 允许清库重建；执行任何清理前必须把现有 learning DB 复制为带日期的备份。
- 不为早期 dogfood 数据设计复杂无损迁移，但重 ingest 的**长期运行时语义**必须正确。
- KnowledgeItem 身份调整属于 ADR-0002 / ADR-0005 的后果补充；实现前写 ADR，不在代码里暗自决定。

### Replay 与真机验收

- 工具契约必须进入 Replay 指纹；旧 cassette 失效是正确行为，不以“保旧键”为优先目标。
- 可先用 fake provider 完成确定性测试；真实模型 cassette 集中作为 HITL 验收，由用户操作或放行 `.env`。
- Replay 指纹不得包含密钥原文；配置只记录可公开的模型、adapter 与契约摘要。

### Web 范围

- 本 PRD 只实现 P1 所需的异步流式抓取、真实大小上限和结构化获取地基。
- 正文抽取质量、`web_search`、浏览器 fallback 与 MCP adapter 另立 Web Acquisition PRD，经用户确认后实施。
- 不把搜索 / MCP 扩展混入单一流式限制 bugfix。

### 开发纪律

- 每个 issue 是一个可独立验收的竖切；确定性核心先写失败测试。
- 每个竖切同步更新相关权威文档，不把文档治理推迟到所有代码完成之后。
- 五门固定为 Ruff、format check、Pyright、import-linter、pytest；实现后当前基线为静态四门全绿、
  `714 passed / 4 failed`，pytest 失败均由两份待真录 cassette 直接或派生触发。

## Proposed Vertical Slices

以下拆分已确认并发布到 `issues/`：

1. **SH-S0 文档权威基线**（AFK，done 2026-07-16）
   - 对齐 README、architecture、Context Compression PRD、自进化 PRD 与 skeleton ledger 的事实状态。
   - 发布本 PRD 与已确认 issue；不改未拍板的领域决策。
2. **SH-S1 资源身份 + 原子快照替换**（实现完成，HITL 重建待验收）
   - 修同名本地文件碰撞；定义重 ingest 的稳定身份、旧 item 清理与关联账 reconciliation。
   - 先审议 ADR-0007；允许备份后清库切换 schema。预研确认 S1 的快照提交与 S5 必须复用同一个
     transaction seam，S1 不得落一次性私有事务 helper。
3. **SH-S2 显式 scope 解析失败拒答**（实现完成）
   - 区分“用户没指定范围”和“指定了但没解析成功”，后者零出题、零 provider 调用。
4. **SH-S3 异步流式 Web Fetch**（实现完成）
   - 流式限制解压后字节数；保留 SSRF、逐跳重定向、超时与内容类型守卫。
   - 落结构化获取结果，为独立 Web Acquisition PRD 提供稳定 seam。
5. **SH-S4 Replay 执行指纹**（实现完成，HITL cassette 待重录）
   - 把 tool specs 等执行契约纳入指纹；旧 cassette 明确失效；fake 回放先绿。
   - 真实 cassette 重录作为本 issue 的 HITL 收口。
6. **SH-S5 学习状态原子提交**（实现完成）
   - 复用 S1 已建立的 transaction seam，让一次判决的 Learning Memory、Difficulty、AskedQuestions
     相关状态共享事务语义；不另建第二套事务模块。
7. **SH-S6 Durable Trace 失败语义**（实现完成）
   - 区分 durable processor 与 best-effort observer；trace 写失败不得静默报告成功。
8. **SH-S7 完整出站上下文预算**（实现完成）
   - 预算覆盖 tool specs、循环追加消息、工具结果和持久题目历史。
9. **SH-S8 一路答对的难度演化**（实现完成，HITL cassette 待重录）
   - 补齐自进化 User Story 12，并增加难度激活真实 cassette 验收。
10. **SH-S9 真实审批门**（实现完成，HITL 终端验收待执行）
    - 先交付 CLI 可筛选候选的真实行为；suspend/resume 作为独立后续竖切，不伪装成已完成。
11. **SH-S10 全量收口与完成审计**（HITL，blocked by: S1-S9）
    - 五门、全部 eval、cassette、清库重建 dogfood；更新所有权威文档与残余风险报告。

## Out of Scope

- `web_search` 的具体搜索供应商、浏览器自动化、MCP transport 与任意 MCP 工具动态挂载。
- 跨资源 concept_key 归并、复习排期、向量数据库、Web 前端和语音。
- 为一次性早期 dogfood 数据实现复杂无损迁移。

## Proposed Design: SH-S2 Scope 三态契约

> 本节已随 issue 拆分由用户确认，是 SH-S2 的实现契约。

当前 `resource_ids: list[str] | None` 把三种不同用户意图压成了两种值：

- 用户没点材料 → `None`，正确语义是全库。
- 用户点了且匹配成功 → 非空列表，正确语义是精确过滤。
- 用户点了但 LLM 没从目录匹配出 ID → 仍只能 `None`，代码误当成全库。

仅修改 tool description 无法修复第三支，因为 handler 看不到“这个 None 是没指定还是没识别”。建议把
`start_quiz` 的 scope 改成**必填判别联合**，由 LLM 只做语义分类，代码冻结三态后果：

```text
all        用户没有指定材料；resource_ids 必须为空 → 全库
selected   用户指定且成功匹配；resource_ids 必须非空 → exact-id 过滤
unresolved 用户指定但无法匹配；requested_label 必须非空 → 立即拒答
```

锁定语义：

- `scope` 在 `start_quiz` 工具 schema 中必填，不提供隐式 `all` 默认；模型遗漏或字段组合非法走既有
  ToolRegistry 参数校验 / ModelRetry，让模型修正，不能静默扩大范围。
- `all` 只允许用户确实没指定材料时使用；`selected` 至少一个 ID；`unresolved` 保存用户点名短语供拒答
  与 trace 解释，不携 ID。
- `assess_once` 接收同一结构化 scope，而不是再拆成 `resource_ids + scope_requested` 两个可能互相矛盾的
  参数；直接 CLI quiz 显式构造 `all`。
- `unresolved` 在读取候选池 / 选题 / 出题前发 `ASSESSMENT_REFUSED(reason="unresolved_scope")`，
  payload 带 `requested_label`；零出题、零判卷、零记忆写入。
- `selected` 的 ID 在库中全未命中仍走现有 `empty_scope`；这是“曾解析出 ID 但当前库无匹配”，与
  “无法解析用户意图”分开。
- `ASSESSMENT_STARTED` payload 记录 scope mode、requested label（如有）、resource IDs 与命中数，供
  trace / eval 解释代码究竟按什么范围执行。
- 现有 ReAct cassette 因工具 schema 攸关决策发生变化应失效，并在 SH-S4 的执行指纹规则下重录；不为
  保旧 cassette 保留 `None` 歧义。

验收至少覆盖：

1. `all` 可考全库；`selected` 只考指定资源。
2. `unresolved` 即使全库非空也拒答，provider 调用数为 0。
3. 非法组合（如 selected + 空 IDs）在工具参数校验处失败，不进入 workflow。
4. 真录 ReAct 轨迹：用户点名不存在的材料 → `start_quiz(scope=unresolved)` → 零 `QUESTION_ASKED`。
5. 用户说“随便考我” → `start_quiz(scope=all)`，证明 fail-closed 没有破坏合法全库意图。

## Completion Evidence

- 每个已发布 issue 的 acceptance criteria 全部有测试、trace、持久化重开或真机记录证明。
- 清库操作前存在可恢复备份；新库从真实材料重建成功。
- 所有旧 cassette 的保留 / 重录 / 废弃都有明确清单，不存在静默沿用旧工具契约的回放。
- 五门全绿，且新增测试确实覆盖上述失败场景，而不只是维持原有 `682 passed`。
- README、CONTEXT、architecture、ADR、PRD 与 skeleton ledger 对当前状态无冲突。
