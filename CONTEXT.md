# TheGrandQuiz

个人学习辅助工具（作者本人是用户 #1），同时以较为全面的技术栈和工程深度作为简历项目。场景与 Runtime 是同一产品的两面：学习场景提供真实使用价值，Runtime 提供工程展示价值。

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
静默跳到 current。旧 quote 无法唯一定位时保留为 unresolved 审计项，不猜测、不让既有 item 从考核池消失。
_Avoid_: 只有 quote 的幽灵引文、LLM 自报数据库身份、用模糊匹配伪造精确 locator

**Agentic Search**（ADR-0008，DS-S4 已实现开放查询基座）:
开放 ReAct 对 current DocumentNode 的渐进式查询路径：大纲 → FTS5 稀疏搜索 → 展开/有界读取 → 精确 citation。
LLM 决定读哪一节，代码强制 exact scope、稳定排序、累计读取预算、untrusted 标记与 read-before-cite。它不替代
核心考核 workflow 的确定性选题，也不是通用 RAG/向量检索层。
_Avoid_: 点名失败后扩大到全库、未读取正文就引用、一次倾倒全文、让自由 ReAct 接管考核状态机

**Web Acquisition**（WA-S1–S3 已实现，WA-S4 待真实验收）:
学习材料进入 Reader 之前的外部发现与规范化边界。`web_search` 只返回 `SearchResult[]` 候选，用户或开放 ReAct 选择 URL 后，Fetch 才产生 `FetchedDocument`；随后仍走确定性的 Reader → KnowledgeItem 审批 → 全局 KB workflow。SearchProvider 可拔插，首个 adapter 是可选 SearXNG endpoint；不配置时工具不注册，SearXNG 服务或 Docker 不是基础运行依赖。
_Avoid_: 搜索结果自动批量抓取/入库、让 search adapter 直接写 KB、把 SearXNG/Docker 变成强依赖、把 Web Search 与库内 DocumentNode Agentic Search 混为一谈

**FetchedDocument**:
网络或其他 acquisition adapter 归一化后的不可信文档信封：requested/final/canonical URL、标题、规范化正文、content type/hash、adapter/extractor 指纹和结构化质量结论。HTML 由 Trafilatura 产 Markdown；空壳、过短、导航、登录与 bot challenge 页面 fail closed。requested URL 仍是 LearningResource identity，正文不进入 trace。
_Avoid_: 原始 HTTP response 对象、把 canonical URL 悄悄改成资源身份、质量失败后继续调用 Reader、把完整网页 body 塞进事件

**KnowledgeRelation**（ADR-0008，DS-S5 eval-gated，当前关闭）:
两个 source-grounded KnowledgeItem 之间的类型化语义边，首批关系限定为 prerequisite / related /
contradicts，必须携带 confidence、evidence provenance、抽取版本、trace id 与 review status。它是 LLM 推断的
可撤销投影，不与 DocumentNode 的确定性结构边混同，也不藏在 metadata JSON 中。
_Avoid_: 把文档层级自动提升为知识图谱、无置信度/无出处的自由三元组、用关系边替代 KnowledgeItem 身份

**LearningTask**（已消解，ADR-0005）:
~~学习主题的容器与考核范围~~——**已废弃**。真机 dogfood 暴露"会话绑一个启动标题 = 换标题换库"把持久库切成孤岛（PRD #2）。现收敛到**全局 KB 单池**：不再有独立 `LearningTask` 实体、无 `tasks` 表；资源**内容寻址**（`resource_id = derive_id(url)`，同 URL 全局唯一、`INSERT OR REPLACE` 去重），进同一持久库。会话是无状态对话前端，`react`/`quiz` 的 `title` 降为可选横幅（只打印、不进派生 / 分区）。出题 / 判卷语言从 task 属性移入 [Preference Memory]（`question_language`，跨全库个人设置）。跨会话 / 跨材料的薄弱概念天然互见（[Learning Memory] 锚定 KnowledgeItem、本就不按 task 分区——ADR-0003 期望终态）。
_Avoid_: 任务、待办；"标题锁库"；把 title 当知识范围（scope 走查询期软过滤，见全局 KB PRD）

**ActivityEvent**:
工具内发生的学习动作记录：审批资源、深读完成、答题对错、跳过、要求重考。答题记录是最高置信信号——"会不会"由考核结果说话，"学没学"不采集。
_Avoid_: 学习时长、阅读进度等任何工具外行为指标

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

**判决**:
判卷的结构化产出：对 / 勉强 / 错三值 + 薄弱概念指认（指向 KnowledgeItem）+ 所引证据。选择题为确定性比对；开放问答与追问由 LLM 判卷且必须引用出题锚定的 evidence 比对，判决依据进 trace。
_Avoid_: 评分、分数（无分数概念，三值即全部语义）

**审批门**:
人工决策点。当前 CLI 在"深读产出 → 入库"之间阻塞展示 KnowledgeItem 的概念、摘要、证据与置信度，
用户逐项剔除后才原子替换知识快照；请求与决策都进入事件脊柱。跨进程形态的目标是暂停 turn、持久待决
状态并凭 token 恢复，但该 suspend/resume 原语尚未交付。二期 discovery 回归后资源级审批复用同一语义。
_Avoid_: 把阻塞 CLI prompt 冒充成已实现的可暂停/恢复 turn；把审批退化成固定 keep-all
