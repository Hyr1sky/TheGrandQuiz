# LW-S2 — Web 视觉方向选择与设计语言

Status: done
Type: HITL

## Parent

[PRD：Local-first Web 学习工作台](../PRD.md)

## What to build

围绕同一 Article Workspace 内容和交互生成三种可比较的一屏视觉方案，均遵守“纸面学习工作台 ×
软质仪器控件 × Evidence 玻璃遮罩”，但在版式、色彩和批注关系上形成真正差异。用户选择后把布局、
token、字体、状态、动效、responsive 和 accessibility 约束固化到
`docs/design/web-visual-language.md`，作为 React 实现权威。

不得直接使用 `localtemp/prompt4frontend` 的矛盾 hard rules；可保留其纸张、压印和少量 tactile 控件灵感。

## Acceptance criteria

- [x] 三个方案使用相同信息架构和内容，能够公平比较
- [x] 方案不退化为传统 dashboard 或标准 chat 双栏
- [x] citation、运行状态、文章层级和 Evidence reveal 在静态画面中可理解
- [x] 每个方案说明字体、颜色、密度、动效和可访问性取舍
- [x] 用户明确选择或要求混合后才开始正式 React 视觉实现
- [x] 选择结果落盘为项目专用 visual language，不依赖聊天上下文

## Blocked by

LW-S1 的稳定 HTTP 信息架构。

## Completion

用户选择第 3 个“墨迹星图”方向，并要求亮色/暗色双主题。视觉权威已落盘至
`docs/design/web-visual-language.md`，两张参考稿位于 `docs/design/assets/`。
