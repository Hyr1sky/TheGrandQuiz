# WR-O4 — 罗盘观测抽屉与真实 dogfood

Status: done
Type: AFK

## Parent

[Web Runtime 上下文连续性与运行观测](../PRD.md)

## Acceptance criteria

- [x] 状态栏罗盘可明确 hover/click 打开与关闭观测抽屉
- [x] 抽屉渐进展示当前 trace 摘要、token、model/tool/error/recovery 与 span 时间线
- [x] loading/disconnected/no-trace/error 均有明确状态
- [x] 键盘可达，dialog/region 语义与 focus 不破坏 Chat/Assessment
- [x] 不渲染 raw prompt、工具参数、模型正文或材料正文
- [x] Vitest + TypeScript + build 通过
- [x] 真实 Chat → Assessment dogfood 可在抽屉观察，并记录 trace_id
- [x] 更新开发日志与 dogfood 指南
