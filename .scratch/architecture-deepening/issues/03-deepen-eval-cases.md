# AD-S3 — Deepen per-kind Eval case Module

Status: ready-for-agent
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

让 ingest、assess、react 三类 Eval case 分别拥有严格配置、校验与 solve 行为。公共 Eval runner 只消费统一结果；
非法 kind、枚举、字段或缺少必填配置时在加载阶段 fail closed，不能静默运行成默认 workflow。

## Acceptance criteria

- [ ] 未知 case kind 在加载时失败
- [ ] 未知 provider、focus、fixture、source 与 react fixture 在加载时失败
- [ ] 每类 case 只接受自己的 setup 字段，未知字段失败
- [ ] 合法 17 case 的事件、Tier-1/Tier-2 verdict、trace 和成本保持不变
- [ ] 公共 runner 不分支读取各类 case 的专属 setup 字段
- [ ] grader 与 runner 不再依赖 lazy import cycle
- [ ] invalid-config 测试先红后绿，全量 Eval 测试通过

## Blocked by

None - can start immediately

