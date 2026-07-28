# WR-O1 — 当前材料进入 Chat turn context

Status: done
Type: AFK

## Parent

[Web Runtime 上下文连续性与运行观测](../PRD.md)

## Acceptance criteria

- [x] Message contract 接受 optional `active_resource_id`
- [x] 后端验证 exact resource；未知 id fail closed
- [x] 动态 system Partition 只注入 resource id，不拼接不可信标题或修改用户消息/history
- [x] 无 active resource 时保持现有全局 KB 行为
- [x] HTTP/SSE seam 测试证明“当前材料”能解析到所选资源
- [x] App 把顶栏当前 resource id 传给 ChatPanel
