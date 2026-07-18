# GAS-S1 — 自然问答基线与验收契约

Status: done（2026-07-19；自然失败基线与 case15 grounding/cost scorer 已冻结）
Type: AFK

## Parent

[PRD：自然材料问答与 Agentic Search 成本收口](../PRD.md)

## What to build

把当前“自然询问材料但没有精确 citation”的行为固定为可重复失败基线，并建立后续实现必须满足的端到端契约。
自然问题不得包含任何工具名；评测应同时观察用户可见答案、selected scope、搜索/读取/citation 事件，以及模型调用、
工具调用、tokens、最大 prompt 和正文读取占比。

覆盖 PRD User Stories：1–3、5、8–10、14–18、21、23–25、27。

## Acceptance criteria

- [x] 新增一个不包含工具名的自然材料问答 eval case，使用确定性的合成 KB 和明确 selected resource scope
- [x] 回归测试在实现前能复现当前缺陷：流程可以返回文字或 node id，但没有可逐字解析的 exact node citation
- [x] 规则断言定义 search → covering read → citation 的顺序、read-before-cite、current revision 和 exact scope
- [x] 规则断言能从 trace 计算 model/tool calls、累计 tokens、最大 prompt、已读字符与 revision 读取占比
- [x] 验收阈值固定为 model calls ≤4、累计 tokens ≤45,000、读取占比 ≤25%、exact citations ≥1
- [x] invalid scope、no evidence、budget exhausted 的期望均为零伪造 citation 和结构化 fail-closed 结果
- [x] 基线数据记录自然 trace 的 8 model / 10 tool / 82,581 tokens / 0 citation，以及显式 trace 的对照数据
- [x] 测试不依赖私有 helper、具体模型措辞或手工编辑 cassette

## Completion evidence

- case15 scorer 对零工具直答、scope/read/citation/usage/调用/tokens/读取比例建立确定性反证门。
- 真实 case15 为 3 model calls / 10,282 tokens / 1 exact citation；旧自然基线为 8 / 82,581 / 0。

## Blocked by

None - can start immediately
