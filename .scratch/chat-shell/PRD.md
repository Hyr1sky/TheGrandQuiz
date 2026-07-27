# Spec: Chat Shell — Agent-driven workspace with conversational entry point

Status: ready-for-agent
Triage: ready-for-agent

## Problem Statement

TheGrandQuiz Web 目前是两个独立的表单式 workspace（文章问答 + 考核），通过 tab 手动切换。用户看到的
是"两个 workflow 产品"，而不是"一个 agent 驱动的学习工具"。项目的核心卖点——可观测的 Agent Runtime、
自由 ReAct 编排——在 Web 端完全不可见。

具体痛点：
- 没有自然语言入口，用户无法像 CLI `grandquiz react` 那样和 agent 对话。
- 两个 workspace 完全隔离，考核中想问 agent 一个概念解释做不到。
- 阅读时需要滚动整个页面才能看到大纲和内容，三栏没有独立滚动。
- 左侧大纲字体不统一，视觉实现偏离设计稿。
- 整体布局 AI 味浓、neumorphism 过度、响应式体验差。

## Solution

将 Web 端重构为 **agent 驱动的三栏工作台**：右栏是持续的对话流（用户通过自然语言与 ReAct agent
交互），主内容面由 agent 的工具调用驱动切换（文章阅读 / 考核 / 搜索结果），左栏根据主面板上下文
自动展示相关导航（文档大纲 / 考核进度 / 搜索候选）。

后端新增有状态的 ReAct session（`ChatManager`），前端通过 SSE 事件流驱动对话渲染和面板切换。
确定性 workflow（考核）保持独立执行，ReAct 只负责理解意图和路由。

## User Stories

1. 作为用户，我想在右栏输入自然语言和 agent 对话，以便不需要理解系统有哪些 tab 和表单。
2. 作为用户，我想在对话中说"考我几道选择题"，agent 在主面板启动考核 workflow，以便考核入口是自然的。
3. 作为用户，我想在考核进行中仍然能在右栏和 agent 对话（比如问"这个概念能解释一下吗"），以便考核不阻断对话。
4. 作为用户，我想看到 agent 调用工具的过程（搜索材料、阅读节点、生成回答），以便理解 agent 在做什么。
5. 作为用户，我想在右栏看到格式化的 agent 回复（markdown 标题、列表、代码块），以便阅读体验不是纯文本。
6. 作为用户，我想让主面板在 agent 触发导航时自动切换到对应内容（文章 / 考核 / 搜索），以便不需要手动切 tab。
7. 作为用户，我想在主面板处于考核状态时看到试卷式布局（题干 + 材料 + 选项 + 结果 + 下一题/结束按钮），以便考核体验像答题而不像填表。
8. 作为用户，我想让左栏自动展示和主面板相关的导航（阅读时是文档大纲、考核时是进度、搜索时是候选列表），以便左栏始终有用。
9. 作为用户，我想能手动切换左栏内容（比如考核时也能看文档大纲），以便不被自动切换限制。
10. 作为用户，我想刷新页面后开始一个新的对话 session，但学习进度（薄弱状态、已问过台账等）不丢失，以便每次打开都是干净的对话起点。
11. 作为用户，我想在对话中使用指代和承接（"那第四章呢"、"刚才那个概念"），agent 能理解上下文，以便对话是自然连续的。
12. 作为用户，我想在对话中看到 agent 调用导航工具时的过渡提示（"正在为你准备考核..."），以便理解面板为什么切换了。
13. 作为用户，我想在底栏看到当前状态定位和运行轨迹（罗盘式导航），以便知道自己在阅读/考核/搜索的哪个阶段。
14. 作为用户，我想让三栏各自独立滚动，以便阅读长文章时不需要滚动整个页面。
15. 作为用户，我想让字体在同一区域内保持统一（阅读区宋体、界面区黑体），以便视觉不混乱。
16. 作为用户，我想让 neumorphism 阴影只出现在少量核心控件上，以便界面不像 AI 生成的模板。
17. 作为开发者，我想让 ChatManager 和 RunManager / AssessmentManager 互不依赖，以便新增能力不需要改动现有 manager。
18. 作为开发者，我想让导航工具只在 Web API 层注册、CLI 和 eval 不可见，以便不影响 CLI 行为和 eval 确定性。
19. 作为开发者，我想让 ReAct session 的所有事件进入 trace.db，以便 agent 的路由决策和工具调用可审计。
20. 作为开发者，我想让前端通过 SSE 事件流驱动对话渲染，以便前端不需要发明新的通信协议。
21. 作为开发者，我想让考核 workflow 继续通过现有 AssessmentManager endpoint 独立执行，以便确定性保证不松动。
22. 作为开发者，我想复用现有 API 测试模式（fake provider + 临时 SQLite + TestClient），以便 ChatManager 的测试和已有测试一致。

## Implementation Decisions

### 1. 后端：新增 ChatManager

新增 `ChatManager`，和 `RunManager` / `AssessmentManager` 并列在 `interfaces/api/` 层。它持有一个
`Runner` 实例（带 `ContextBuilder`、`ToolRegistry`、`HookManager`），管理有状态的 ReAct session。

- 一个 session = 一次浏览器访问。单用户本地工具，内存中至多一个 active session。
- 进程重启丢对话历史（可接受，同 CLI 行为），领域状态（Learning Memory、KB 等）持久化不丢。
- `Runner._history` 跨轮累积，`SummarizingHistoryCompressor` 按现有机制压缩。

### 2. 后端：ReAct endpoint

新增三个 endpoint：

- `POST /api/v1/chat/sessions` — 创建 session（销毁旧 session），返回 `session_id`。
- `POST /api/v1/chat/sessions/{session_id}/messages` — 发一条用户消息，触发 `Runner.run_agent_turn`，返回 `202` + `turn_id`。
- `GET /api/v1/chat/sessions/{session_id}/events` — SSE 事件流，投影 `AgentEvent` 为 chat 专用 UI 事件。

### 3. 后端：导航工具

在 `interfaces/api/` 层定义导航工具（`start_assessment`、`open_article` 等），仅 Web API 层注册：

```
CLI  composition:  register_learning_tools(registry)
Web  composition:  register_learning_tools(registry) + register_navigation_tools(registry)
```

导航工具的 handler 不执行重逻辑——验证参数 + 发事件 + 返回确认文本给 LLM。前端监听
`TOOL_CALL_STARTED(name="start_assessment")` 等事件驱动面板切换。

### 4. 后端：Manager 协作——前端是协调者

`ChatManager` 和 `AssessmentManager` 不直接调用对方。协作通过前端中转：

- `ChatManager` SSE 推送 `chat.navigation("assessment", {...})` 事件。
- 前端收到后自行调用 `POST /api/v1/assessments` 启动考核。
- 考核结束后，前端在对话流中追加本地摘要。
- agent 通过 `ContextBuilder` 从持久化 Learning Memory 读取最新学情，不依赖对话历史传递考核结果。

### 5. 前端：App shell 三栏布局

```
┌─────────────────────────────────────────────┐
│ [材料选择器]              [全局功能] [主题]   │  顶栏
├──────────┬──────────────┬───────────────────┤
│          │              │ 对话流             │
│ 上下文栏  │  主内容面     │  agent 回复        │
│ 自动跟随  │  文章/考核/   │  工具调用可视化    │
│ 可手动切  │  搜索详情     │                   │
│          │              │ [agent 输入框]      │
├──────────┴──────────────┴───────────────────┤
│ 罗盘导航：当前状态定位 + 运行轨迹              │  底栏
└─────────────────────────────────────────────┘
```

- 三栏各自独立滚动（`overflow: auto`）。
- 右栏 = 持续对话流 + 底部输入框，通过 SSE 事件流驱动渲染。
- 主面板由导航工具事件驱动切换（文章 → 考核 → 搜索等）。
- 左栏自动跟随主面板上下文，用户可手动覆盖。

### 6. 前端：对话流渲染

- 对话流由 SSE 事件驱动，不解析 agent final 文本中的结构。
- `AGENT_TURN_ENDED(output=...)` → 渲染为 agent 回复气泡。
- `TOOL_CALL_STARTED/ENDED` → 渲染为工具调用进度指示。
- `chat.navigation(...)` → 主面板切换 + 对话流中渲染过渡提示。
- agent 文字回复使用 `react-markdown` + `remark-gfm` 渲染 markdown。

### 7. 前端：考核面板重构

考核从独立页面变为嵌入主内容面的试卷式布局：

- 上部：题干 + 相关材料证据区（frosted glass reveal）
- 中部：选项或简答输入
- 下部：判卷结果 + 下一题/结束按钮
- 右栏对话流始终可见，用户可边答题边和 agent 对话。
- 考核仍通过 `POST /api/v1/assessments` + `AssessmentManager` 执行，前端自行调用。

### 8. 视觉修正

对照 `docs/design/web-visual-language.md` 修正 Codex 产出的偏离：

- 统一左栏字体（interface 区域全部使用 `--font-interface`）。
- 收敛 neumorphism 范围（只保留 ask/reveal/theme/compact run 控件的 raised/pressed 效果）。
- 减少 hover 动画幅度（去掉全局 `translateY(-2px)`）。
- 三栏各自 `overflow: auto`，消除全页滚动。

## Testing Decisions

### 后端

- `ChatManager` 通过 FastAPI TestClient + fake provider + 临时 SQLite 测试，模式和
  `test_api_article_workspace.py` / `test_api_assessment_workspace.py` 一致。
- 测试覆盖：session 创建与销毁幂等性、消息发送 → SSE 事件流顺序与终态、导航工具触发事件、
  多轮对话上下文承接（第二条消息能引用第一条的内容）、session 在进程重启后不可恢复（预期行为）。
- 导航工具的注册隔离：验证 CLI composition 不包含导航工具。

### 前端

- 三栏 App shell 用 Vitest + Testing Library 测：对话流渲染、面板切换响应事件、左栏自动切换。
- 复用现有 `FakeEventSource` 模式 mock SSE。
- `react-markdown` 渲染通过 snapshot 或 inline assertion 验证。
- 先有 prior art：`ArticleWorkspace.test.tsx`（SSE 事件流驱动）、`AssessmentWorkspace.test.tsx`
 （状态轮询 + 交互）。

## Out of Scope

- 多用户、登录、权限系统、云托管、远程数据库和公网部署。
- ReAct session 跨进程持久化（进程重启丢对话历史是预期行为）。
- 浏览器端 ReAct 执行（所有 agent 逻辑在后端）。
- Web Acquisition workflow 的 UI（搜索 → 用户选择 → fetch → 审批），留给后续 ticket。
- 底栏罗盘导航的完整设计（S4 视觉打磨阶段落地，本 spec 只预留空间）。
- 流式 token-by-token 输出（LLM 返回 completion.text 一次性结果，"流式感"来自事件序列）。

## Further Notes

- 本 spec 覆盖 local-web PRD 的 LW-S5 之前的重构方向。local-web PRD 的后续交付节点
 （Web Acquisition / 可恢复审批 / 设置页 / v0.1.0 发布门）不受影响，但入口从 tab 切换变为 agent 对话。
- 交付分四个竖切：S1 ChatManager 骨架、S2 前端 App shell、S3 导航工具 + 面板切换、S4 左栏上下文 + 视觉打磨。
- Codex 产出的 API 接线代码（OpenAPI 生成类型、SSE 模式、fake provider 测试模式）保留复用，
  不重写已验证的基建。
