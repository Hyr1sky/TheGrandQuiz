# Local Web 考核工作台开发记录

> 记录日期：2026-07-26
>
> 对应范围：`.scratch/local-web/` 的 LW-S4，并包含 GroundedDocumentAnswer 问句检索加固
>
> 目标：把既有逐题考核 workflow 投影成可恢复、可审计的 Web 交互，同时让阅读问答与所有控件的
> hover、focus、pressed 和 selected 反馈更明确。

## 1. 先修真实阅读问答回归

生产 trace 表明，“agent 的潜在记忆是什么”并非材料缺失：整句 FTS 无命中，旧 fallback 又先使用过宽的
`agent`，导致真正有区分度的“潜在记忆”没有优先进入候选。GroundedDocumentAnswer 现在接受自然语言问题，
在既有 exact resource scope 内用确定性规则提取和排序中英文高信息量短语；它不调用额外模型，也不会在点名
失败后扩大到全库。

同时修复 Article Workspace 的 no-evidence 双重渲染：fail-safe 状态只显示一次，不再把后端兼容性
`answer` 文本重复附加到结果下方。

## 2. 复用领域 workflow，而不是在 API 里重写考核

FastAPI 新增进程内 `AssessmentManager`，通过一个可等待的 Web responder seam 复用
`AssessmentSession`。选题、题型路由、出题、选择题确定性判决、开放题判卷、Learning Memory 和难度记账
仍由领域层负责；HTTP 层只拥有会话生命周期、DTO 和显式 command。

本轮增加以下接口：

```text
POST /api/v1/assessments
GET  /api/v1/assessments/{session_id}
POST /api/v1/assessments/{session_id}/questions/{question_id}/evidence/reveal
POST /api/v1/assessments/{session_id}/questions/{question_id}/answers
POST /api/v1/assessments/{session_id}/next
```

考核始终携带显式 `SelectedScope`，一次只推进一道题。回答和“下一题”command 使用稳定
`request_id` 做幂等保护：网络重试返回同一状态，冲突的第二次写入返回 409，不会静默重复判卷或记账。

## 3. Evidence reveal 成为可审计学习动作

题目证据默认只返回遮罩状态；用户悬停、聚焦或点击后，API 才把已校验的逐字 evidence 投影给前端。
首次揭示会发出 `LearningEvent.EVIDENCE_REVEALED`，trace 记录 question、item 和交互类型，不把原文 quote
复制进事件 payload。重复揭示保持幂等。

## 4. React Assessment Workspace

应用顶部增加“阅读 / 考核”模式切换，考核入口支持：

- 选择明确材料、题数和自适应/选择题/简答题；
- 一次呈现一道题，选择或输入答案后再显式提交；
- 玻璃遮罩 Evidence reveal；
- 展示对/勉强/错、原因、概念状态和按需展开的参考答案；
- 显式进入下一题，完成后开始新一轮；
- 用 `sessionStorage` 保存 session id，刷新后通过 GET 恢复当前状态；
- 在页面保留 `trace_id` 供本地 `trace.db` 审计。

OpenAPI 仍是前后端唯一契约源，生成的 JSON 与 TypeScript schema 已同步更新。

## 5. 交互反馈与视觉验收

全局控件补齐统一的 hover 抬升、focus ring、active 下压、disabled cursor/opacity。题目选项整行可点，
选中时使用高对比 evidence 色边框、左侧强调条和内阴影；亮色与暗色主题使用同一语义 token。

浏览器实际验收覆盖桌面与 390×844 移动 viewport。顶部导航、题目、证据、判决和 trace 在两种主题下均
保持可读；浏览器控制台无 error/warn。Playwright 进一步覆盖阅读问答和完整考核主路径，并显式验证默认
遮罩、hover reveal、作答和判决。

## 6. 测试与边界

后端新增真实临时 SQLite + fake provider 的 API 测试，覆盖逐题等待、揭示幂等/审计、回答 exactly-once、
空 scope 可解释拒绝和开放题显式下一题；前端新增 Testing Library 行为测试与 Playwright 桌面/移动主路径。

自动测试与本地 fixture 都没有读取生产数据库、调用 `.env` provider 或发送材料到外部 LLM。当前
Assessment session registry 仍是单进程内存状态；浏览器刷新可恢复，服务重启后的跨进程恢复留给 LW-S5
可持久审批/恢复竖切。下一开发节点是 Web Acquisition 候选选择与可恢复审批。
