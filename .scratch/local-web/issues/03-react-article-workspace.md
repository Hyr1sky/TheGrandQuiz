# LW-S3 — React Article Workspace 端到端竖切

Status: blocked
Type: AFK

## Parent

[PRD：Local-first Web 学习工作台](../PRD.md)

## What to build

在选定视觉语言下建立 `web/` 的 React/Vite/TypeScript feature-first 工程，使用 OpenAPI 生成 client，
贯通资源选择、outline、节点正文、材料提问、run 状态、SSE 和 citation 定位。首屏是文章工作台，
对话表现为与文档关联的批注/浮层，不是独立 chat 产品。

## Acceptance criteria

- [ ] `web/src` 采用 app/routes/features/shared 边界，Article Workspace 是首个 feature
- [ ] TypeScript client 由 OpenAPI 生成，无手写重复 DTO
- [ ] 用户可选择资源、浏览 outline、读取节点、发起问题并看到阶段进度
- [ ] 回答 citation 可定位 section_path 并展示逐字 quote/context
- [ ] no evidence、provider failure、取消和断线重连有明确状态
- [ ] 键盘导航、焦点、对比度、reduced motion 和窄屏布局可用
- [ ] Vitest/Testing Library 与 Playwright 覆盖主路径
- [ ] 前后端构建与 Python CI 不互相隐式依赖全局工具

## Blocked by

LW-S1 and LW-S2.
