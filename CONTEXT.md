# TheGrandQuiz

个人学习辅助工具（作者本人是用户 #1），同时以较为全面的技术栈和工程深度作为简历项目。场景与 Runtime 是同一产品的两面：学习场景提供真实使用价值，Runtime 提供工程展示价值。

> 文档职责：本文件是领域用词的权威表，回答“这个词在项目里是什么意思”。完整字段与状态不变量见
> [docs/domain-model.md](docs/domain-model.md)，产品边界见 [docs/product.md](docs/product.md)，实现完成证据
> 见 [docs/devrecords/](docs/devrecords/)。条目中的实现状态只是帮助识别语义，不替代 roadmap 或开发记录。

## Language

**产品**:
学习数字人个人辅助工具本身。第一验收维度是作者真的用它学习；第二验收维度是工程质量能支撑简历叙事。
_Avoid_: 把 Runtime 单独当产品、面向外部用户的服务

**Runtime**:
事件总线脊柱上的 Agent 执行内核（runner / trace / hook / replay / eval / recovery）。产品的工程核心、简历叙事的承重部分，但不是独立产品；kernel API 是内部纪律，不是对外契约。
_Avoid_: 框架（不承诺对外 API 稳定性 / semver / 插件文档）

**Eval Quality Gate**:
Eval 层对最终产物的离线质量门，与生产考核的“判决”不是同一概念。Tier-1 用确定性代码核验工具顺序、scope、状态与精确引用；Tier-2 `QualityJudge` 只评预注册 rubric，在通过人工 calibration 后才参与用例 pass/fail，并通过真实 cassette 日常 Replay。两层 verdict、trace 与 token 成本必须分开；首版只给 case15 启用 `grounded_answer`。
_Avoid_: 用 LLM judge 替代规则门、拿 judge 自己的输出当 calibration 真值、在 report 中隐式调用外部模型、让 Eval 自动修改 prompt 或生产数据

**简历价值**:
项目的第二目标：面向 AI 应用 / Agent 工程师岗位的叙事——"手写一套可观测、可评测的 agent runtime，并且自己真的在用"。"全面技术栈"指覆盖完整工程生命周期（异步、事件架构、持久化、流式接口、eval、可观测性、CI），不指框架数量。
_Avoid_: 为凑技术栈引入产品不需要的组件（前端 / 向量库 / k8s 不因简历进入 roadmap）

**学习数字人**:
产品的场景层：输入学习目标 → 发现资源 → 人工审批 → 建知识库 → 调度学习技能（测验 / 面试 / 总结 / 路线）。
_Avoid_: showcase、demo（旧称已纠正——它是给自己用的工具，不是给别人看的演示）

**考核循环**:
产品的核心循环、每日可重复的心跳：学完材料 → 被拷问（quiz / 面试式追问）→ 暴露薄弱概念 → 记入 Learning Memory → 下次优先考薄弱点。纯按需触发（用户说"考我"才考），选题 = 薄弱概念优先掺新概念，无主动提醒、无复习排期。发现、总结、路线规划是支撑它的配角。会话形态为逐题交互的多 turn 循环（出题 → 答 → 判决 → 勉强/错则追问或给正解 → 下一题），非一次性出卷。
_Avoid_: 把六个学习技能当平权功能列表、复习计划（MVP 无排期概念）、批量出卷（一次性生成整份
试卷让用户批量作答；`start_quiz(count=N)` 内**批调度**——按用户要求预定这批 N 题里几道选择几道
简答——仍是逐题交互，不算批量出卷，二者是不同维度）

**薄弱概念**:
考核循环的货币：用户答错或答得勉强的知识点，锚定到具体 KnowledgeItem（不是自由文本标签），带证据（错在哪道题）与时间，存于 Learning Memory，驱动下一轮出题优先级。生命周期是三态状态机：答错/勉强 → `薄弱`；薄弱下答对 → `观察中`；观察中再答对 → `销账`（移出薄弱表）；任一状态再答错/勉强 → 打回 `薄弱`。即"连续答对两次才算掌握"，防蒙对/刚看完的假掌握。
_Avoid_: 错题（薄弱的是概念，不是题目本身）、跨资源的抽象概念（MVP 无此实体）、掌握度分数（用状态机不用连续分）

**KnowledgeItem**:
深读一个资源产出的最小知识单元（概念名 + 摘要 + 证据 + 置信度），资源内唯一。它就是概念同一性的边界：同一知识点出现在两个资源里是两个 item，MVP 不归并（二期以 concept_key 做跨资源别名归并）。证据带结构定位符（section_path 等），既强化 grounding 也锚定 ADR-0008 的 DocumentNode 文档结构树。
_Avoid_: 知识点卡片、笔记

**ResourceRevision**（ADR-0008，DS-S1–S4 已实现）:
LearningResource 某次获批内容的不可变版本，由 resource_id + content_hash 确定性标识，保存当时的原文与
DocumentNode 树。LearningResource 仍按稳定 locator 定位，只把 current_revision_id 指向当前获批版本；旧版本
不参与默认搜索和考核，但保留给历史 trace 与引用解析。
_Avoid_: 把 URL 当内容版本、重 ingest 时原地覆盖后无法解释历史引用、把 revision hash 当 resource_id

**DocumentNode**（ADR-0008，DS-S1–S4 已实现）:
ResourceRevision 内可导航、可精确定位的原文结构节点，形成 document / section / paragraph / table / code 等
父子树，携带 node_id、section_path、顺序与 source span。它回答“原文在哪里、怎样组织”，不回答“知识点之间
是什么语义关系”；KnowledgeItem 可由一条或多条 evidence 锚定 DocumentNode。current revision 的节点进入
FTS5，开放 ReAct 只能通过有界的大纲、搜索、展开、读取工具渐进披露正文。
_Avoid_: chunk（任意 token 窗口）、把章节父子关系称为概念上下位关系、用可重复/可变的 section_path 充当身份

**Evidence**（ADR-0008，DS-S2 已实现）:
KnowledgeItem 对原文的可验证引用，保存 revision_id、node_id、section_path、全局 source span、quote 与
quote hash。新证据必须由代码逐字验证后才能随 snapshot 提交；历史 citation 始终读取声明的 revision，不能
静默跳到 current。Reader 允许把非代码 Markdown 节点中 CommonMark 反斜杠转义后的唯一可见 quote 映射回
raw source offsets，但 Evidence 始终保存原始 source slice；代码节点不做转义映射，零匹配或多匹配仍
fail closed。旧 quote 无法唯一定位时保留为
unresolved 审计项，不猜测、不让既有 item 从考核池消失。
_Avoid_: 只有 quote 的幽灵引文、LLM 自报数据库身份、用模糊匹配伪造精确 locator

**Agentic Search**（ADR-0008，DS-S4 已实现开放查询基座）:
开放 ReAct 对 current DocumentNode 的渐进式查询路径：大纲 → FTS5 稀疏搜索 → 展开/有界读取 → 精确 citation。
LLM 决定读哪一节，代码强制 exact scope、稳定排序、累计读取预算、untrusted 标记与 read-before-cite。它不替代
核心考核 workflow 的确定性选题，也不是通用 RAG/向量检索层。
_Avoid_: 点名失败后扩大到全库、未读取正文就引用、一次倾倒全文、让自由 ReAct 接管考核状态机

**Web Acquisition**（WA-S1–S5 已实现并完成真实 ReAct 验收）:
学习材料进入 Reader 之前的外部发现与规范化边界。`web_search` 只返回 `SearchResult[]` 候选，用户或开放 ReAct 选择 URL 后，Fetch 才产生 `FetchedDocument`；随后仍走确定性的 Reader → KnowledgeItem 审批 → 全局 KB workflow。SearchProvider 可拔插：Tavily 提供无需信用卡的免费 Key 路径，SearXNG 提供可选自托管路径；不配置时工具不注册，SearXNG 服务或 Docker 不是基础运行依赖。两者同时配置必须显式选择 provider，不做隐藏 fallback。`web_search` 的结构化结果显式要求用户选择，真实 case17 证明开放 ReAct 会先结束发现回合，再对选中 URL 进入确定性 Reader / 审批 workflow；登录页失败保持零 KB 污染。
_Avoid_: 搜索结果自动批量抓取/入库、让 search adapter 直接写 KB、把 SearXNG/Docker 变成强依赖、把 Web Search 与库内 DocumentNode Agentic Search 混为一谈

**Local Web Interface**（ADR-0009，LW-S1–S5 + WR-O1–O4 已实现）:
面向本机单用户的正式产品通道：React Article / Assessment Workspace 通过版本化 REST + SSE 调用 FastAPI
interface adapter；长操作形成可查询 run，进度是同一 `AgentEvent` 脊柱的安全 UI projection。它已把资源 →
DocumentNode 大纲/节点 → GroundedDocumentAnswer → 精确 citation 变成空间化阅读体验，并把既有
`AssessmentSession` 投影成显式 scope、一题一步、Evidence reveal 可审计、提交/下一题幂等的考核交互；
顶栏 exact material 已进入 Chat turn context，跨轮 SSE 使用单调 cursor，底部罗盘通过
`TraceObservatory` 安全投影当前 Chat/Assessment 的状态、耗时、token、model/tool/error/recovery 与 span。
Web Acquisition 通过上传 Markdown/Text 或公开 URL 创建持久 run，`queued/running/needs_input/succeeded/
failed/cancelled` 全生命周期与安全 SSE 投影共用事件脊柱；`needs_input` 候选和单次过期 token 可跨服务重启
恢复，审批后才原子提交知识快照。失败以稳定、安全的 `code / stage / reason` 进入 ledger、AgentEvent、
Trace error 统计、CLI 与 Web 管理态，raw exception/quote/正文不进入浏览器投影。历史 trace 浏览和完整
资源/知识点管理仍属后续竖切。默认只监听 loopback，
CLI 继续作为调试、恢复和审计入口。
_Avoid_: 通用数据库 dashboard、浏览器直连 SQLite、把完整内部 AgentEvent/prompt/正文推给浏览器、把
核心考核改成自由 ReAct、在 v0.1.0 假装支持多用户或公网部署

**FetchedDocument**:
网络或其他 acquisition adapter 归一化后的不可信文档信封：requested/final/canonical URL、标题、规范化正文、content type/hash、adapter/extractor 指纹和结构化质量结论。HTML 由 Trafilatura 产 Markdown；空壳、过短、导航、登录与 bot challenge 页面 fail closed。requested URL 仍是 LearningResource identity，正文不进入 trace。
_Avoid_: 原始 HTTP response 对象、把 canonical URL 悄悄改成资源身份、质量失败后继续调用 Reader、把完整网页 body 塞进事件

**KnowledgeRelation**（ADR-0008，DS-S5 eval-gated，当前关闭）:
两个 source-grounded KnowledgeItem 之间的类型化语义边，首批关系限定为 prerequisite / related /
contradicts，必须携带 confidence、evidence provenance、抽取版本、trace id 与 review status。它是 LLM 推断的
可撤销投影，不与 DocumentNode 的确定性结构边混同，也不藏在 metadata JSON 中。
_Avoid_: 把文档层级自动提升为知识图谱、无置信度/无出处的自由三元组、用关系边替代 KnowledgeItem 身份

**LearningTask**（已消解，ADR-0005）:
~~学习主题的容器与考核范围~~——**已废弃**。真机 dogfood 暴露"会话绑一个启动标题 = 换标题换库"
把持久库切成孤岛（PRD #2）。现收敛到**全局 KB 单池**：不再有独立 `LearningTask` 实体、无 `tasks`
表；LearningResource 按规范化的稳定 locator 寻址（ADR-0007），`content_hash` 标识该资源获批内容对应的
ResourceRevision。相同 locator 的重 ingest 通过原子快照提交切换 current revision，而不是把 URL 误称为
内容身份或用 delete-then-insert 替换资源。会话是无状态对话前端，`react`/`quiz` 的 `title` 降为可选横幅
（只打印、不进派生 / 分区）。出题 / 判卷语言从 task 属性移入 [Preference Memory]（`question_language`，
跨全库个人设置）。跨会话 / 跨材料的薄弱概念天然互见（[Learning Memory] 锚定 KnowledgeItem、本就不按
task 分区——ADR-0003 期望终态）。
_Avoid_: 任务、待办；"标题锁库"；把 title 当知识范围（scope 走查询期软过滤，见全局 KB PRD）

**ActivityEvent**:
工具内发生的学习动作记录：审批资源、深读完成、答题对错、跳过、要求重考。答题记录是最高置信信号——"会不会"由考核结果说话，"学没学"不采集。
_Avoid_: 学习时长、阅读进度等任何工具外行为指标

**Learning Fact Journal**（ADR-0010，Learning Model v2 基础闭环已实现）:
同一 AgentEvent 脊柱面向长期学习数据的白名单持久消费者，写入 learning.db。只保留重建
AssessmentAttempt、判决纠正、分类与学习投影所需的 committed facts；完整 prompt、工具 payload、
普通 Chat、token 和无关错误仍只属于 trace.db。它与 Learning Memory / Difficulty / AskedQuestions 的
提交使用 transaction/outbox 边界，防止半状态。
_Avoid_: 第二条事件总线、完整 Trace 副本、把 JSONL 当数据库

**AssessmentAttempt**（Learning Model v2 基础闭环已实现）:
从 Learning Fact Journal 按 trace_id + assessment_span_id 确定性重建的一次考核事实投影，记录题目路由、
输入媒介、答案形态、initial/final verdict、Evidence reveal、耗时、生成/判卷版本及当前已批准的 demand
validation 引用。它可删除重建，不是判卷 workflow 的第二个写入口。尚无生产者或消费者的信心、提示、
intended demand 与 diagnosis 只留在未来蓝图，不进入当前 Attempt。
_Avoid_: 直接双写的领域表、修改历史判决、把未来字段提前塞进当前契约

**Learning Vocabulary**（Learning Model v2 v1 已实现）:
封闭行为维度、受控增长 term 和开放 candidate 三层词表。稳定身份是 namespace + key；模型候选在审核前
不驱动选题或状态机。领域/技术标签只经 TagAssignment 关联，seed 在仓库，用户扩展存 learning.db。
_Avoid_: 自由 tag 直接控制行为、按显示名寻址、同义词自动合并、把 tag 当概念同一性

**LearnerProjection**（Learning Model v2 v1 已实现）:
从 committed attempts、Learning Memory 和 DifficultyLedger 重建的分析读模型，汇总考核次数、判决分布、
闭卷次数、当前薄弱状态、难度和经独立 DemandValidator 验证的能力证据。销账/复发、自信校准
和错因指标仍是未来蓝图。它不生成单一 mastery_score，也不反写状态机。
_Avoid_: 第二套 Learning Memory、用 intended demand 自证能力、把 not_in_memory 解释成已经掌握

**Learning Memory**:
考核循环的持久层：薄弱概念 × 最近表现（概念锚定 KnowledgeItem，三值判决历史）。MVP 仅有的领域记忆，选题优先级的唯一数据源。
_Avoid_: 进度记忆、掌握度（无独立掌握度模型，由判决历史推断）

**Preference Memory**:
用户偏好（题型偏好、追问强度、语言），带 confidence。MVP 保留，挂在 after_turn 由 LLM 判断写入。
_Avoid_: 设置、配置（偏好是学习出来的，不是用户填表的）

注：roadmap 原列的 Resource Memory 已并入 [KnowledgeItem]（不重复造实体）；Session Memory 是 kernel 的会话历史（kernel 概念，非 domain 记忆），不在本表。

**考官**:
产品在考核循环中的人格面：system prompt 层面的语气与追问风格设定。语音与形象不属于考官的定义（语音绑定面试场景门后等候）。
_Avoid_: 数字人形象、虚拟人

**题型路由**:
考核中按概念状态选择题型的规则：首次接触的概念用选择题热身，默认开放问答，薄弱概念复考用追问深挖。
_Avoid_: 单一题型、随机题型

**AssessmentPlan**:
一次多题考核的规范化有序计划：每个位置只保存一个用户题型意图。Chat、CLI 与 FastAPI adapter 都必须
先把“轮数 / 单题型 / 分段题型”收敛为该计划，再由 workflow 逐题调用同一个
`resolve_question_type`；interface 不得自行展开或压扁题型序列。
_Avoid_: Web 只传 rounds + 单一 question_type；CLI/Web 分别维护题型展开规则

**QuestionSpec**:
一道开放题或追问的唯一题目规格：题干、至少一个带原文锚点的 `ExpectedPoint`、只回答本题的
`reference_answer` 与题目 Evidence。Grader 只能依据该规格判卷，不能重新从整个 KnowledgeItem
猜测本题想考什么。
_Avoid_: 出题看 summary、判卷看另一组 Evidence、参考答案再拼整个 item

**判决**:
判卷的结构化产出：对 / 勉强 / 错三值 + 命中/缺失评分点 + 受控诊断 + 所引证据。选择题为确定性比对；
开放问答与追问由 LLM 对 QuestionSpec 的评分点逐项判断，代码校验评分点完整覆盖、互不重叠且与三值
结论一致。薄弱概念指认和状态转移仍由代码完成；判决依据进 trace，并由 CLI/Web 使用同一事件投影。
_Avoid_: 评分、分数（无分数概念，三值即全部语义）

**审批门**:
人工决策点。CLI adapter 可阻塞展示候选；Web adapter 把深读后的 `PreparedIngest` 持久为
`needs_input`，浏览器凭单次、可过期 token 在服务重启后继续逐项筛选。两者都只在决策后原子替换知识快照，
请求与决策都进入事件脊柱。同步 CLI 与可恢复 Web 是同一领域语义的两种 interface adapter。
_Avoid_: 让 HTTP 请求一直阻塞等待人工输入；在审批前写入资源或 KnowledgeItem；把审批退化成固定 keep-all
