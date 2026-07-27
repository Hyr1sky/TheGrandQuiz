# 03 — 导航工具 + 考核面板切换

**What to build:** 对话中说"考我几道选择题"→ agent 调用 `start_assessment` 导航工具 → 主面板切到考核试卷布局 → 逐题答完后回到文章。右栏对话流在考核期间始终可见。

**Blocked by:** 02-frontend-app-shell

**Status:** ready-for-agent

- [ ] 后端：`interfaces/api/` 层定义导航工具模块（`start_assessment`、`open_article`），handler 验证参数 + 发事件 + 返回确认文本
- [ ] 后端：`register_navigation_tools(registry)` 函数，仅 Web API composition 调用
- [ ] 后端：验证 CLI composition 不包含导航工具（测试断言）
- [ ] 后端：导航工具事件投影为 `chat.navigation` UI 事件类型，通过 SSE 推送
- [ ] 前端：`ChatPanel` 监听 `chat.navigation` 事件，通知 App shell 切换主面板状态
- [ ] 前端：主面板状态机（`reading` | `assessment` | ...），由导航事件和用户操作共同驱动
- [ ] 前端：考核面板重构为试卷式布局嵌入主内容面——上部题干+材料证据区、中部选项/简答、下部判卷结果+下一题/结束按钮
- [ ] 前端：考核仍通过 `POST /api/v1/assessments` + `AssessmentManager` 执行（前端是协调者）
- [ ] 前端：考核完成后在对话流追加本地摘要消息（"本轮完成：N 题中答对 M 题"）
- [ ] 前端：`open_article` 导航工具让主面板切回文章阅读
- [ ] Vitest 测试：导航事件触发面板切换、考核面板渲染和交互
- [ ] 后端测试：导航工具注册隔离、导航事件投影
