# PRD：架构 Deepening（考核循环 / Learning persistence / Eval case / 权威文档）

Status: done（2026-07-23；AD-S1–S4 全部落地，静态四门与 841 tests 全绿）
Triage: ready-for-human（仅归档复核；无待实现 issue）

## Problem Statement

TheGrandQuiz 的事件脊柱、确定性考核 workflow、全局 KB 与 Document Structure 已经稳定运行，但持续演进
暴露出四处架构摩擦：

1. 单题考核仍是正确的确定性 workflow，但其 Interface 随 Preference Memory、scope、跨会话去重与难度
   等能力增长；CLI、ReAct tool 与 Eval 调用者必须重复学习会话状态、随机种子、取消、恢复和汇总规则。
2. LearningResource/KnowledgeItem store、Learning Memory、Preference Memory、AskedQuestions 与 Difficulty
   共享一个 LearningDatabase，却由调用者解包、逐个关闭；原子判决还需要从 Adapter 私有属性推断 transaction
   owner，连接所有权与 transaction seam 缺少 Locality。
3. Eval Quality Gate 的 Case Interface 同时容纳 ingest、assess、react 三类可选字段；解析器对未知 kind、
   provider、focus、fixture 等配置静默回落，可能让写错的用例运行成另一条 workflow 并产生假绿。
4. CONTEXT/architecture 已修正主要 ADR-0007 漂移，但生产 docstring、CLI 文案和少量当前态描述仍残留
   LearningTask、内容寻址或未交付 Web Acquisition 等旧事实，降低 AI-navigability。

这些问题尚未造成测试失败，但会让下一项能力横向扩大 Interface 与 test surface。目标不是把文件切小，
而是把已有行为收入更 deep 的 Module，让调用者获得 Leverage、维护者获得 Locality。

## Solution

在不改变用户可观察考核语义、Replay 契约和领域身份的前提下，按四个垂直切片深化现有 Module：

1. 让考核循环 Module 持有跨轮会话状态和稳定依赖，CLI、ReAct tool 与 Eval 通过同一 Seam 发起单轮或多轮考核。
2. 让一个 Learning persistence Module 明确拥有 LearningDatabase、五类持久 Adapter 的生命周期与判决事务，
   同时保留各领域账本各自的 Interface 和 Dict/SQLite Adapter parity。
3. 让每类 Eval case 拥有自己的严格配置、校验与 solve 行为；公共 runner 只消费统一结果，非法配置 fail closed。
4. 清理当前态文档、docstring 与 CLI 文案，使它们与 CONTEXT 和 ADR-0005/0007/0008 一致。

Document Structure、Reader 单 subagent executor、Dict/SQLite 双 Adapter 不是本轮重构目标。

## User Stories

1. 作为学习者，我希望 CLI quiz 与 ReAct `start_quiz` 对同一批题遵守相同的选题、去重和难度规则。
2. 作为学习者，我取消一次作答时，希望两种入口都不把空答案写成判决。
3. 作为学习者，我希望指定材料、题型和 focus 后，多轮考核不会在入口之间产生语义差异。
4. 作为学习者，我希望跨会话 AskedQuestions 与会话内覆盖优先继续各司其职。
5. 作为维护者，我希望增加新的考核依赖时只修改考核循环 Module，而不是同步修改所有调用者。
6. 作为维护者，我希望 CLI 与 ReAct Adapter 只负责输入输出投影，不复制考核 workflow。
7. 作为维护者，我希望测试通过考核循环的公开 Interface 验证行为，而不是反复组装十三项依赖。
8. 作为维护者，我希望 LearningDatabase 的 owner 唯一明确，调用者不需要记住五个关闭动作。
9. 作为维护者，我希望新增一个持久账本时，CLI 生命周期接线只在一个 Module 内变化。
10. 作为维护者，我希望一次判决的 Learning Memory、AskedQuestions 与 Difficulty 写入共享明确的
    transaction seam，而不是依赖私有属性反射。
11. 作为维护者，我希望 Dict 与 SQLite Adapter 在成功、失败、回滚和重试上继续 parity。
12. 作为 Eval 作者，我希望未知 case kind 在加载时立即失败，不能悄悄当成 ingest。
13. 作为 Eval 作者，我希望拼错 provider、focus、fixture 或 source 时立即得到可定位错误。
14. 作为 Eval 作者，我希望 ingest、assess、react 只暴露各自合法字段，不需要理解一张宽而可空的 Case Interface。
15. 作为 Eval 维护者，我希望新增 case kind 时只增加一个深 Module，并通过统一结果接入现有 Tier-1/Tier-2 报告。
16. 作为维护者，我希望 Tier-1 与 Tier-2 verdict、trace 和成本分列语义保持不变。
17. 作为未来 Agent，我希望 CONTEXT、architecture、生产 docstring 与 CLI 文案描述同一个当前系统。
18. 作为项目作者，我希望本轮重构保持静态四门和全量 pytest 通过，且不要求重录无行为变化的 cassette。
19. 作为项目作者，我希望每个切片可以独立回滚，不形成一次不可审查的大爆炸提交。
20. 作为项目作者，我希望现有 README 与开源发布清单的并行改动不被本任务覆盖或误提交。

## Implementation Decisions

- ADR-0004 继续有效：核心考核循环仍是确定性 workflow；LLM 只负责出题与开放题判卷。
- `assess_once` 的单题语义继续作为内部确定性骨架；deepening 优先收拢稳定依赖、会话内去重、种子推进与
  多轮调用，不把核心循环交给自由 ReAct。
- CLI 与 ReAct tool 继续是 Adapter：它们保留呈现、参数翻译和通道特有错误投影，不再拥有重复的领域状态。
- Learning persistence deepening 只集中连接 owner、生命周期与 transaction；不因 Protocol+Dict+SQLite
  形状相似而合并 Learning Memory、Preference Memory、AskedQuestions 或 Difficulty 的领域职责。
- Dict 与 SQLite 是两个真实 Adapter，现有 Seam 保留。
- transaction owner 必须显式传递或由拥有者 Module 提供；禁止继续通过任意参与者私有属性反射推断。
- Eval case 配置采用按 kind 区分的严格结构；未知字段、未知枚举和缺少必填字段 fail closed。
- 公共 Eval runner 不知道某类 case 的专属 setup 字段，只消费统一的 solve 结果与既有质量配置。
- HTML/text 报告是统一结果的两个真实 Adapter；报告内容和 pass/fail 语义保持不变。
- 当前态文档只修改权威文档、生产 docstring 与用户可见文案；历史 ADR、devrecords 与已完成 PRD 保留时间语境。
- 不引入新的全局领域实体，不修改 SQLite schema，不创建第二个 Document Structure Adapter。
- 不修改或提交当前工作区已有的 README 与开源发布清单改动。

## Testing Decisions

- 所有确定性核心改动采用一条行为测试一个红—绿循环，不批量先写实现形状测试。
- 考核循环通过 CLI/ReAct tool 可观察结果、事件序列、Learning Memory 末态、去重与种子确定性验证。
- persistence 通过公开生命周期 Interface 验证一次关闭、跨 Adapter 共享数据、事务回滚和重试 parity。
- Eval case 先补非法配置 fail-closed 测试，再迁移合法 17 case，避免重构把既有静默回落固化。
- 报告测试继续断言 Tier-1/Tier-2 verdict、成本与 trace 分列，不断言内部文件或私有类名。
- 文档切片使用精确术语回归测试，只检查当前态文档/生产文案，不扫描历史记录。
- 每个切片先跑受影响测试；收口时运行 Ruff、format check、Pyright、import-linter 与全量 pytest。
- 无用户可观察消息、tool schema、prompt 或 Provider messages 变化时，现有 cassette 不重录；若实际发生变化，
  必须显式列出并按 Replay 指纹规则处理。

## Out of Scope

- 改写考核状态机、题型路由、难度算法或 Learning Memory 生命周期。
- 把核心考核循环改为自由 ReAct。
- 跨资源 CanonicalConcept、KnowledgeRelation、向量检索或图数据库。
- 拆分健康的 Document Structure Module。
- 抽取只有 Reader 一个调用者的通用 subagent executor。
- 实现审批门/Responder 的跨进程 suspend-resume。
- 修改 SQLite schema、生产数据库或真实 cassette 内容。
- 开源发布、LICENSE、tag、GitHub Release 或 README 发布指南。

## Further Notes

- 2026-07-22 架构审查报告位于 OS 临时目录，不作为仓库产物提交。
- 本轮先做 Interface 收窄和责任 Locality；若实现证据显示某候选无法在不改变行为的前提下 Deepen，应在对应
  issue 留下反证并停止该切片，而不是为完成清单强造抽象。
- 完成顺序以风险递增：考核循环 tracer → persistence owner → Eval strict cases → 当前态文档 → 全量收口。

## Completion Evidence

- `AssessmentSession` 成为 CLI quiz 与 ReAct `start_quiz` 共用的多轮考核 Interface，会话覆盖台账与
  确定性种子推进不再由两个 Adapter 重复持有。
- `LearningPersistence` 唯一拥有共享数据库及五类具名 SQLite Adapter，生产入口只关闭一次；
  `TransactionParticipant.transaction_owner` 取代私有属性反射。
- `IngestCase` / `AssessCase` / `ReactCase` 严格解析并各自求解，公共 runner 只消费统一
  `SolveResult`；非法配置在加载阶段 fail closed。
- 当前态 roadmap、生产 docstring 与 ingest 文案已对齐 ADR-0005/0007/0008；历史记录未改写。
- 删除性证据：`start_quiz_tool.py`、CLI `quiz.py` 与 Eval `harness.py` 合计新增 88 行、删除 327 行；
  新 Module 隐藏了调用者知识，没有形成仅转发参数的浅层包装。
- 完整质量门：Ruff、format check、Pyright（0 errors）、import-linter（1 contract kept）与
  `841 passed` 全绿；Replay/cassette 用例包含在全量测试中。
- 用户原有 `README.md` 与 `docs/open-source-release-checklist.md` 改动未进入本任务提交。
