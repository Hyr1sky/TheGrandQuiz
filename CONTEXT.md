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

**Eval Surface**:
一类需要独立质量证据的产品能力切面，例如 acquisition、reader/grounding、grounded answer、question
generation 或 answer grading。Surface 定义“评什么”，不等同于 case 的执行 kind，也不规定必须使用
Tier-1、Tier-2 或某个 Provider；同一个 surface 可以由多种数据集和评测层覆盖。
_Avoid_: 用 Python 模块名或单条 case 充当产品能力分类、为每个 surface 强制增加 LLM judge

**Eval Subject Snapshot**:
一次评测中被测系统配置的不可变身份，冻结足以解释结果差异的 prompt、Provider/model/thinking、tool schema、
预算/重试策略与相关 workflow/harness 版本。它描述“测的是哪个系统组合”，不包含数据集、运行结果、密钥或
完整 prompt 正文；Replay cassette 是执行证据，不替代 Subject Snapshot。
_Avoid_: 只记模型名、从当前环境事后猜测配置、把 Provider Profile 与完整被测系统身份混为一谈

**Eval Experiment**:
在同一 Dataset Snapshot、suite policy 和指标下，对 baseline 与 candidate 两个 Eval Subject Snapshot 做配对
比较的可审计运行。Development Gold 用于错误分析与候选筛选；Release Holdout 只用于最终晋升门，揭盲后
立即降级。Experiment 可以证明候选在预注册切片上更好，但不会自行修改生产配置。
_Avoid_: 比较两个使用不同数据或阈值的独立报告、看到单一总分上涨就忽略失败切片与成本

**Promotion Decision**:
人类依据 Eval Experiment、回归、成本和新 Release Holdout 作出的采用、拒绝或保留实验决定。决定引用
baseline/candidate、数据与报告身份，并保留回滚目标；LLM 可以提出候选和解释证据，但不能自行晋升。
_Avoid_: 无监督改 prompt、把 Development Gold 通过称为发布、覆盖旧配置后失去回滚身份

**Development Gold Set**:
已经有人类可信标签、并且结果已被用于错误分析、Prompt 调整或规则设计的题目与答卷。它适合持续回归和
复现已知误差，但因为开发者已经看过，不能再次证明对未知数据的泛化能力；合成 challenge 只能作为其中的
exploratory 分层。
_Avoid_: 把“有人工标签”等同于“仍然未见”，或把已揭盲开发集反复运行到通过后称为 release gate

**Release Holdout**:
Gold 数据中在调参期间封存的切片；QuestionSpec、答卷、逐点人工标签与 hash 均在生产 Grader 运行前
冻结。一旦查看输出并据此修改系统，该切片立即降级为 Development Gold。下一轮发布必须使用新的未见
切片，并同时报告答卷数、unique QuestionSpec 数和评分点数。
_Avoid_: 只按 point 数夸大样本量、把同题近重复答案拆到 dev/holdout 两边、失败后改标签或 rubric 再重跑

**Grading Benchmark**:
Gold 数据、development/holdout 切分、固定 Provider/model/thinking/prompt、指标阈值、隐私审批、冻结哈希
与 Record/Replay 协议的完整组合。Benchmark 是评测系统；Gold Set 是有标签数据；Holdout 是一种受限用法。
_Avoid_: 把某个 JSON 报告、某一批 12 条样本或单一准确率称为完整 benchmark

**Answer Provenance**:
开放题答卷的来源身份：`unassisted_human / assisted_human / model / synthetic_oracle`。只有未见 rubric、
盲于生产输出且经人工终审的 `unassisted_human` 可以进入 release gate；模型生成、模型辅助和合成 oracle
即使有人类标签也只能作为 exploratory。来源身份随答卷进入 Compilation 与 Calibration Report，不能靠
目录名或口头约定推断。
_Avoid_: 用模型答卷补足人类 Holdout 数量、把人工修改过的模型草稿冒充独立人类回答、报告里只写 eligible
布尔值却丢失为什么不 eligible

**Calibration Sample Identity**:
判卷校准中，`question_id` 标识被回答的同一个 `QuestionSpec`，`sample_id` 标识某位答题者的一份独立答案。
多位答题者可以用不同 `sample_id` 回答同一 `question_id`；人工 annotation 永远按 `sample_id` 绑定答案。
旧数据未显式提供 `question_id` 时，按 `question_id = sample_id` 解释。规模报告必须分别统计 unique question
和 response sample，不能把同题多答误报成新题。
_Avoid_: 用题号同时充当所有答卷 ID、按 `question_id` 覆盖另一位答题者、让一份 annotation 同时标注多份答案

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
深读一个资源产出的最小知识单元（概念名 + 摘要 + 证据 + 置信度），资源内唯一。它就是当前概念同一性的
边界：同一知识点出现在两个资源里是两个 item；只有出现明确产品消费者和 Eval 证据后，才重新评估
concept_key 或其他跨资源归并方案。证据带结构定位符（section_path 等），既强化 grounding 也锚定
ADR-0008 的 DocumentNode 文档结构树。
_Avoid_: 知识点卡片、笔记

**Assessment Mode**（规划中）:
考核怎样组织知识范围的策略：`atomic` 只考一个 KnowledgeItem，`composite` 沿已验证关系联合多个 item，
`exploratory`（产品文案可称“混沌模式”）允许提出本地知识图之外的问题。它与题型、难度和输入方式正交。
_Avoid_: 用 temperature 定义模式、把复合模式称为批量出卷、把 exploratory 理解成无约束随机提问

**Knowledge Relation Assertion**（实验候选）:
一条带来源、Evidence、置信度和裁决状态的 KnowledgeItem 间语义关系主张；历史主张可以保留，但只有当前
有效投影能够参与复合考核。它尚不是当前 KB 的生产事实，必须先由复合考核 Prototype 与 Eval 证明价值。
_Avoid_: DocumentNode 父子边、无来源的图边、CanonicalConcept、把整张图当唯一真相源

**Knowledge Frontier Entry**（规划中）:
混沌考核发现、但尚未被已审批材料支持的外部知识主张或考察点。它可以保存交互与待核验状态，但在补充
材料并晋升为 KnowledgeItem 前，不得直接形成正式薄弱事实。
_Avoid_: KnowledgeItem、薄弱概念、模型说过一次就成立的知识事实

**ResourceRevision**（ADR-0008，DS-S1–S4 已实现）:
LearningResource 某次获批内容的不可变版本，由 resource_id + content_hash 确定性标识，保存当时的原文与
DocumentNode 树。LearningResource 仍按稳定 locator 定位，只把 current_revision_id 指向当前获批版本；旧版本
不参与默认搜索和考核，但保留给历史 trace 与引用解析。
_Avoid_: 把 URL 当内容版本、重 ingest 时原地覆盖后无法解释历史引用、把 revision hash 当 resource_id

**RecognitionLexicon**（v0.5）:
由某个已获批 ResourceRevision 的原文、DocumentNode 与 KnowledgeItem 派生出的语音识别词表投影；其中一个
RecognitionLexiconEntry 只表示该投影内的一个候选术语及其来源。投影可由相同 revision 和构建规则重新生成，
不是新的学习事实；人工确认的拼写、读音或别名属于独立的持久输入，重建时再叠加，不能随投影删除。
_Avoid_: 全局原始总词表、把单个 Entry 当整张词表、把 Provider 专有参数写入学习领域、丢失人工修正

**TranscriptionHints**（v0.5）:
一次 VoiceRun 根据精确考核范围从 RecognitionLexicon 选出的有界术语快照。它随本次运行冻结并可在 Trace 中
追溯，再由 Provider adapter 映射为厂商所需的词表或上下文参数；它不是长期知识事实，也不反向修改词表。
是否向 Provider 应用这份快照由本地设置 `asr_material_hints_enabled` 决定：环境变量只提供首次默认，Web
可以热更新 Preference Memory；设置变更只影响随后创建的 VoiceRun，不能改写已经接受的运行。
_Avoid_: 每次请求携带整库术语、跨材料污染、运行后按新词表静默重写历史输入、把提示词表当正确答案泄露

**Difficulty Mode**（v0.5 Web Settings）:
用户对下一次出题的全局倾向：`foundation / adaptive / challenge` 分别在知识点当前 1–5 档上做 -1 / 0 / +1
的有界问题时偏移。它是显式 Preference，不改写 DifficultyLedger，也不伪造学习证据；难度演化仍只由真实
判决与既有确定性规则记账。
_Avoid_: 把全库知识点直接批量改档、把偏好后的有效档写回历史、用一个全局难度替代逐 item 难度台账

**Local Settings**（v0.5 Web Settings）:
本机单用户的安全配置投影。浏览器主题留在当前浏览器；出题语言、Difficulty Mode 与材料词表开关进入
learning.db 的 Preference Memory 并供 CLI/Web 共用；LLM/ASR Provider 只投影配置状态、模型和 endpoint
host，Key 原文仍只存在于 `.env`，不经 HTTP 返回，也不能在 Web 中编辑。
_Avoid_: 把 API Key 存入 localStorage、通过 GET 回显掩码前原文、让各页面各自读取环境变量

**VoiceRun**（v0.5）:
一次完整录音从后端接受、外部转写到可审查草稿的应用运行。草稿只有经用户确认或修改，并通过既有
Assessment answer submission 后才成为正式答案；VoiceRun 不拥有出题、判卷或学习记账。
_Avoid_: 语音版 Assessment、把 ASR 草稿直接当答案、把 Provider task id 当 voice_run_id、取消后接纳迟到结果

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

**AnswerEvidenceUnit**:
一次开放题判卷内，由代码把学习者答案按句末标点和换行确定性切出的、互不重叠且保持原文顺序的临时切片。
每个单元带版本化 offset ID；Grader 只选择 `answer_evidence_ids`，代码校验 ID 后解析出可读原文。
它不是 KnowledgeItem 的材料 `Evidence`，不作为独立领域实体持久化；报告中的 `answer_evidence` 是兼容
展示字段，不是模型可以自由填写的事实。
_Avoid_: 让模型复制、改写或用省略号拼答案证据；把一次判卷的答案切片写成第二套知识库 Evidence

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
`TraceObservatory` 把当前 Chat/Assessment 投影成版本化 `SafeTraceRunV1`：浏览器只接收有限
`operation / phase / stage / reason_code / attempt`、安全 token/latency 与未知事件的 `other` 降级；raw event
type、payload 和完整 spans 只保留在内部 TraceStore、CLI 审计与 Tier-1 Eval。
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
一条 accepted Knowledge Relation Assertion 对 current revisions 仍有效时，在 Active Knowledge Graph 中形成的
可撤销关系投影，不是独立真相源。首批实验 vocabulary 限定为 `prerequisite_of / contrasts_with / implements /
failure_mode_of / tradeoff_with`；Prototype 与 Eval 晋升前不存在生产 KnowledgeRelation。
_Avoid_: `related_to`、把文档层级自动提升为知识图谱、无出处的自由三元组、用关系边替代 KnowledgeItem 身份

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
不驱动选题或状态机。人工批准且 active 的 KnowledgeClassification 可经唯一 facet consumer 在考核启动时
冻结为 exact item IDs；空匹配直接拒绝，不回退整篇材料。领域/技术标签只经 TagAssignment 关联，seed
在仓库，用户扩展存 learning.db。
_Avoid_: 自由 tag 直接控制行为、按显示名寻址、同义词自动合并、把 tag 当概念同一性

**Grading Eval Candidate**（v0.3 已实现）:
从 append-only VerdictCorrection 确定性投影的本地反馈候选，保留题目、答案、模型初判、人类终判、原因和
版本。它明确是非盲标、需要隐私审核且不能直接打开发布 gate；人工盲标校准样本另行维护并直接运行生产
grader。
_Avoid_: 把用户纠正冒充盲标金标准、自动上传或自动晋升为发布 Eval、建立第二份可变事实源

**Material Discovery Batch / Candidate**（v0.4 已实现）:
用户显式给出主题后，由 SearchProvider 返回的持久候选收件箱。Batch 保存搜索策略、adapter、成功/失败状态；
Candidate 保存 provider 顺序、规范 URL、确定性质量标记、资格与人工决定。发现阶段严格只读：不抓正文、
不调用 Reader、不写 LearningResource；批准只授权既有 Acquisition，仍要经过知识点审批。
_Avoid_: 定时主动搜索、模型自动批准、用虚构相关性分数排序、让搜索直接写 KB

**Eval Inbox Candidate / Dataset Snapshot**（v0.4 已实现）:
判决纠正或显式导入的人工盲标进入本地隐私审核收件箱。相同来源的新 payload supersede 旧候选；只有
active + approved 候选可以组成以内容 SHA-256 寻址的不可变快照。盲于模型输出且标签完整的样本可计入
release gate，判决纠正固定为 exploratory。快照是获批输入的冻结版本，不是新的学习事实源。
_Avoid_: 自动上传、自动训练、修改历史快照、把 correction 计入 blind gate、把 operational trace 当数据集

**Calibration Source Pack / Dataset Compilation**:
Source Pack 是本地、人工拥有的冻结证据包，包含密封 QuestionSpec、独立答卷、终审标签与逐文件 SHA-256；
它不是运行时数据库。Dataset Compilation 是对该证据包做完整性校验后的确定性产物，只把已终审、盲于
模型输出且 rubric 无争议的记录转换为 `GradingCalibrationSample`，并显式保留排除项及理由。Compilation
进入 Eval Inbox 后仍须完成隐私审核，才能形成不可变 Dataset Snapshot。
_Avoid_: 直接运行未冻结 YAML、跳过人工终审或隐私审核、删除争议样本而不保存排除理由

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
`reference_answer`、题目 Evidence，以及可选的 `critical_point_ids`。Grader 只能依据该规格判卷，不能
重新从整个 KnowledgeItem 猜测本题想考什么。核心点评级必须在看到学习者答案前随题目规格冻结；为空时
表示当前 rubric 没有声明核心点，而不是运行时再让模型猜。
_Avoid_: 出题看 summary、判卷看另一组 Evidence、参考答案再拼整个 item

**ExpectedPoint**:
一道题必须检验的语义不变量，而不是参考答案所采用的唯一技术实现。定义、机制类问题可以要求明确的
核心机制；设计、比较、评估类问题应允许满足同一目标和约束的合理替代方案。具体框架、组件或算法只有在
题面明确限定时才能成为必答项，否则只能作为 `reference_answer` 中的示例。一个 ExpectedPoint
只表达一个可独立判断的语义不变量；“定位责任层 + 枚举全部扩展职责”等独立必答条件应拆分，
不得用一个过载评分点迫使 Grader 在“全或无”之间猜测。新题默认保持 flat atomic point；历史实验题
可能带 Required Claims，但该字段不再由出题入口生成，也不代表更高质量的默认 rubric。
_Avoid_: 把“BM25 + dense + reranker”等推荐实现直接写成所有合理方案都必须逐字命中的评分点

**Required Claim**:
ExpectedPoint 内部最小、可独立核验的必要语义断言；同一点的全部 claims 固定为 all-of，学习者 Evidence
必须逐条支持。真实 Development Gold 实验未证明其优于 flat point，因此当前仅为历史兼容与可审计实验
字段：新题 Prompt 不再要求它，显式载入或历史 Provider 响应中的旧 QuestionSpec 仍可回放。
_Avoid_: 参考答案措辞、可选实现、any-of/threshold/exception 树、把可分别计分的题意藏进 claims

**判卷澄清（Grading Clarification，实验 gate 后）**:
开放题初判明确为 `uncertain`，且只有一个 missing point 的升级会改变代码三值时，面向学习者提出一次
只针对该 point 的补充问题。补充回答是新的用户 Evidence，不是第二个隐藏 Judge；原答与补答合并后只
重判一次，仍不确定则进入 `needs_review`，不得写 Learning Memory。当前只实现纯领域 planner/state
machine；Holdout 03 的 30 条生产判卷中 `uncertain=0`，因此尚未接入 AssessmentSession、CLI 或 Web。
首个二分类原型证明 grading matched/missing Gold 不能直接派生追问标签；下一实验必须用独立 Interaction
Gold 区分 `no_support / ambiguous_support / direct_or_equivalent_support`，最后一种进入 `needs_review` 而非
追问或自动改判。当前 12 条 owner-accepted Interaction Gold 只用于 Development Prototype，不是生产词表
或 release holdout；真实三态原型 direct support 4/4、ambiguity 0/2，整体 gate 失败，不能接生产。
_Avoid_: 按 reason 字符串猜不确定性、用 grading Gold 代替 Interaction Gold、每题都追问、循环追问、初判先记账、把内部 retry 冒充用户澄清

**用户判卷申诉（User-initiated Assessment Appeal，Web 已实现）**:
开放题完成初判后，由学习者主动提交的一次补充说明。原始 `answer_text` 不覆盖；系统把原答与补充按稳定格式
交给同一个 `QuestionSpec` / Grader 重判，并把结果写成追加式 Verdict Correction，再由代码重放学习状态。
它不是自动 [判卷澄清]：不预测 ambiguity、不自动发问，也不证明 Interaction classifier 已过 gate。
_Avoid_: 允许连续补答直到变对、覆盖原答、绕过 Verdict Correction 直接修改 Learning Memory

**核心评分点（Critical Expected Point）**:
缺失后足以让整题判为“错”的 ExpectedPoint，由 `QuestionSpec.critical_point_ids` 引用。LLM 仍只逐点判断
命中/缺失；代码确定性聚合：全命中为“对”，零命中或缺任一核心点为“错”，其余为“勉强”。核心性属于
出题 rubric，不属于答案解释，必须在密封题目时预注册；旧样本没有该字段时不能看过模型结果后补标来改善
指标。
_Avoid_: 让 Grader 自己决定哪些点关键、按命中比例设任意阈值、事后为迁就某次输出补核心点

**合理替代方案（Valid Alternative）**:
没有复述参考实现，但满足题目目标、必要约束和 Evidence 所支持语义的作答。它可以使用不同机制组合；只要
没有违反安全、隔离、正确性等硬不变量，就不能仅因技术路径不同被判错。
_Avoid_: 用与参考答案的词面或组件重合度代替语义充分性

**评分标准过约束（Rubric Overconstraint）**:
`ExpectedPoint` 把可选实现、偏好方案或题面未声明的细节误升格为必答条件，导致合理替代方案产生假阴性。
发现后当前样本退出质量门，不能事后修改 rubric 迁就已见答案；修订版应由新的独立答案和反例重新验证。
_Avoid_: 把 rubric 缺陷记成学习者错误，或修改人工标签让既有质量门通过

**Rubric Adjudication**:
对“答案错误、Grader 错误、评分标准过约束、题目描述不足”进行归因的人工裁决。普通 Grader 只按既有
rubric 判卷；Rubric Critic 只能在争议路径检查合理替代方案和 rubric 适用性，不能自动改写最终判决。
_Avoid_: 每题都用第二个模型重复同一 rubric，或让 Rubric Critic 冒充新的生产判卷入口

**判决**:
判卷的结构化产出：对 / 勉强 / 错三值 + 命中/缺失评分点 + 受控诊断 + 所引证据。选择题为确定性比对；
开放问答与追问由 LLM 对 QuestionSpec 的 flat atomic 评分点逐项做语义判断，并选择
AnswerEvidenceUnit ID。显式载入或历史 Provider 响应中的 Required Claims 题仍可按固定 all-of 回放，
但新题 Prompt 不再要求 claims。代码校验
评分点/claim 完整覆盖、ID 唯一性与归属，再解析出精确原文，并根据预先冻结的核心点推导最终三值。
模型自报三值仅作为审计字段，不驱动
Learning Memory；薄弱概念指认和状态
转移仍由代码完成。判决依据进 trace，并由 CLI/Web 使用同一事件投影。
_Avoid_: 评分、分数（无分数概念，三值即全部语义）

**审批门**:
人工决策点。CLI adapter 可阻塞展示候选；Web adapter 把深读后的 `PreparedIngest` 持久为
`needs_input`，浏览器凭单次、可过期 token 在服务重启后继续逐项筛选。两者都只在决策后原子替换知识快照，
请求与决策都进入事件脊柱。同步 CLI 与可恢复 Web 是同一领域语义的两种 interface adapter。
_Avoid_: 让 HTTP 请求一直阻塞等待人工输入；在审批前写入资源或 KnowledgeItem；把审批退化成固定 keep-all
