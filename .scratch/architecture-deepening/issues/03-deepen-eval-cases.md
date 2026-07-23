# AD-S3 — Deepen per-kind Eval case Module

Status: done
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

让 ingest、assess、react 三类 Eval case 分别拥有严格配置、校验与 solve 行为。公共 Eval runner 只消费统一结果；
非法 kind、枚举、字段或缺少必填配置时在加载阶段 fail closed，不能静默运行成默认 workflow。

## Acceptance criteria

- [x] 未知 case kind 在加载时失败
- [x] 未知 provider、focus、fixture、source 与 react fixture 在加载时失败
- [x] 每类 case 只接受自己的 setup 字段，未知字段失败
- [x] 合法 17 case 的事件、Tier-1/Tier-2 verdict、trace 和成本保持不变
- [x] 公共 runner 不分支读取各类 case 的专属 setup 字段
- [x] grader 与 runner 不再依赖 lazy import cycle
- [x] invalid-config 测试先红后绿，全量 Eval 测试通过

## Blocked by

None - can start immediately

## Evidence

- 新增严格的 `IngestCase` / `AssessCase` / `ReactCase` discriminated parser；Pydantic
  `extra="forbid"` 与严格枚举在 solve 前 fail closed。
- 三类 solver 只接受自己的 Case 类型；公共 runner 只读共同的 case 元数据、quality 投影与统一
  `SolveResult`。
- `SolveResult` 与共享 fixture 已从 harness 提取，grader 不再 import harness；`GRADERS` 可在模块
  顶层正常导入，删除了运行期 lazy import cycle。
- 红灯：未知 kind、拼错 assess provider、ingest 携带 assess 字段三条测试均先失败。
- 绿灯：Eval、Tier-2 与 CLI report 回归 `45 passed`；其中 17 条合法 YAML 的既有 verdict/trace/
  成本断言保持全绿。
- 静态验证：受影响文件 Ruff、format check、Pyright 全绿；harness 从 1588 行降至约 1370 行。
