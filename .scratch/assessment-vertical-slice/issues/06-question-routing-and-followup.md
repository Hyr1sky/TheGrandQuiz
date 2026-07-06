# M3.4 — 题型路由 + 追问

Status: done（路由 + MC 确定性判卷 + 追问 + 后置给正解 + eval case 8，commit 60d6b4c，CI 全绿）
Type: AFK

> **考核竖切主干（M3.1→M3.4）至此闭合**，8 个 eval 用例全覆盖。纯确定性路由/判卷、无人机边界。
> 剩余为 M4-M8 的 kernel 层加硬（hooks/context/recovery/Memory-SQLite/eval-harness）+ 交互 CLI（台账 #6）。

## Parent

[PRD: 考核竖切 MVP](../PRD.md)

## What to build

让拷问有层次——题型按概念状态路由，答不好就深挖。题型路由（确定性代码，按概念在 Learning Memory 中的状态选题型）：首次接触的概念用选择题热身、默认开放问答、薄弱概念复考用追问深挖。判决为"勉强 / 错"时触发追问或给出正解。

这是考核竖切的最后一块，补全 CONTEXT.md 的"题型路由"与"追问"语义。

## Acceptance criteria

- [ ] 题型路由：首次接触概念出选择题，薄弱概念复考走追问（eval case 8）
- [ ] 判决为勉强 / 错 → 触发追问或给出正解
- [ ] 路由决策由确定性代码按概念状态做，可在 trace 上断言
- [ ] eval case 8 在事件 / trace 流上可断言
- [ ] CI 全绿

## Blocked by

- [05 — 薄弱记忆 + 状态机 + 薄弱优先复考](05-weak-memory-and-state-machine.md)
