# 02 — 前端 App shell 三栏重构

**What to build:** 前端从 tab 切换重构为三栏工作台：左栏（暂时固定为文档大纲）+ 主面板（暂时固定为文章阅读）+ 右栏对话流。右栏接 ChatManager SSE，能和 agent 对话并看到 markdown 格式回复。

**Blocked by:** 01-chat-manager-skeleton

**Status:** ready-for-agent

- [ ] `App.tsx` 重构：去掉 mode tab 切换，改为三栏 grid 布局（左 / 中 / 右各自 `overflow: auto`，独立滚动）
- [ ] 右栏 `ChatPanel` 组件：对话消息列表 + 底部输入框
- [ ] 页面加载时 `POST /api/v1/chat/sessions` 创建 session
- [ ] 用户输入发 `POST /api/v1/chat/sessions/{id}/messages`，订阅 SSE 事件流
- [ ] SSE 事件 → 对话流渲染映射：`chat.turn_started` → 加载指示、`chat.tool_call` → 工具调用进度、`chat.turn_ended(output)` → agent 回复气泡
- [ ] agent 回复使用 `react-markdown` + `remark-gfm` 渲染
- [ ] 用户消息渲染为右对齐气泡
- [ ] 主面板暂时复用现有 `ArticleWorkspace` 的阅读部分（大纲 + 文章内容），不含原有的问答表单和 run-trail
- [ ] 顶栏：材料选择器 + 主题切换（从 fixed position 改为 grid area）
- [ ] 底栏：预留空间（暂时保留简化版运行轨迹，罗盘在 S4 落地）
- [ ] 添加 `react-markdown` 和 `remark-gfm` 依赖
- [ ] Vitest 测试：对话流渲染、SSE 事件驱动、markdown 格式验证，复用 FakeEventSource 模式
