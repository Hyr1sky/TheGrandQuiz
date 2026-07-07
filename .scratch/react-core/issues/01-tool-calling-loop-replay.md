# R1-S1 — Replay-safe tool-calling runner 循环

Status: done（merge 至 main 4806509；五门全绿 335 passed ×3 无 flaky；golden cassette 向后兼容确认；run_turn/assess_once 空 diff）
Type: AFK

> 终审记：单串行验证员（4 类 mutation 各杀对应测试后撤销、pytest 连跑 2 次无 flaky）。巧思：replay_key 改
> model_dump(exclude_none=True) 使纯文本消息序列化逐字节不变 → 磁盘 golden cassette 照命中（mutation 去掉即
> ReplayMiss 红，验证为向后兼容命门）。build_span_tree 零改动（新 span 沿用 .started/.ended）。工具报错走 M6
> RecoveryPolicy（SKIP 回灌错误让 LLM 换路 / FATAL 冒泡）、before_tool 挂 M4、max_iterations 大声失败。
> run_turn(M1) 体零删除、assessment.py 空 diff、recovery/hooks 空 diff（M4/M6 复用非重写）。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build

给 runner 加一个**有界的 tool-calling 循环**（自由 ReAct 的机制层），并让整轮**零 token 可 replay**。这是 R1 的
地基竖切——只用一个平凡确定性工具打通"LLM → 调工具 → 结果回灌 → 再想 → final"，证明循环 + span 树 + replay，
不碰考官（那是 S2）。

## 锁定设计（不留给实现猜）

- **消息 / Completion 带 tool_calls**（OpenAI 兼容 function-calling 形状）：
  - `Message` 支持 assistant 消息带 `tool_calls`（list of `{id, name, arguments}`）+ `role="tool"` 结果消息（带 `tool_call_id` + content）。
  - `Completion` 增可选 `tool_calls`：provider 返回**要么** final 文本、**要么** 一批 tool_calls。
  - **replay 不变**：tool 选择就是这次 completion 的输出，走同一 `ReplayProvider`（`replay_key=hash(messages)+role+model` 已覆盖——tool_call 与结果作为消息进入下一轮 messages，分支被录下即确定）。`Cassette`/Record/Replay 扩展到能录/放带 tool_calls 的 completion。
- **tool 注册表 `kernel/tools.py`**（零 import domain）：`Tool = name + description + pydantic 入参 schema + async handler`；`ToolRegistry.register(tool)` / dispatch：按 name 找 tool、pydantic 校验 arguments（失败 → ModelRetry 有界重试，复用缝 3）→ 调 handler → 返回结果。
- **runner 循环**（新方法如 `run_agent_turn`，**不改** M1 的 `run_turn`）：发 `AGENT_TURN`（根 span）→ 循环 {`provider.complete`(MODEL span) → 有 tool_calls 则每个发 `TOOL_CALL` span、经注册表执行、把 tool_call + tool 结果追加进 messages → 继续；无 tool_calls（final 文本）则终止}。**有界**：`max_iterations`（防失控循环，超限 → 大声失败，非静默截断）。
- **接住加硬层**：工具 handler 抛异常 → 经 **M6 `RecoveryPolicy`** 裁决（`DEGRADED` → 把错误作为 tool 结果回灌让 LLM 重试/换路；`FATAL`/未知 → 闭合 span 后冒泡）；工具执行前经 **M4 `HookManager.run_before("tool_call", args)`** 挂点（S1 可不注册真 interceptor，但挂点要在——给 S2 的注入/审批留位）。
- **span 类型**：新增 kernel `EventType` `AGENT_TURN_STARTED/ENDED`、`TOOL_CALL_STARTED/ENDED`；generation 复用现有 MODEL span。TOOL_CALL span payload 按 OTel 形状（tool name / arguments / result / error）。parent 嵌套：AGENT_TURN → [MODEL, TOOL_CALL, MODEL, …]。
- **1 个平凡确定性工具**（放测试/组装点，不进 kernel）：如 `echo(text)` 或计数器——纯确定、不需 domain，证明循环。
- **确定性**：`max_iterations` 有界；Clock/RNG 注入；tool 选择走 ReplayProvider。

## Acceptance criteria

- [ ] `Message`/`Completion`/`Provider` 支持 tool_calls（function-calling 形状）；`echo` provider 与 `ReplayProvider`/`Cassette` 能录放带 tool_calls 的 completion
- [ ] `kernel/tools.py` `ToolRegistry`（零 import domain，lint-imports 绿）：注册 + 按 name dispatch + pydantic 入参校验（畸形 args → ModelRetry 有界重试）
- [ ] runner `run_agent_turn`：有界 tool 循环（LLM→tool→结果回灌→final），`max_iterations` 超限大声失败；`run_turn`(M1) 不改
- [ ] `AGENT_TURN`/`TOOL_CALL` span 上脊柱、成树（parent 嵌套正确、started/ended 配对、错误挂 span）
- [ ] 工具报错经 M6 `RecoveryPolicy` 裁决；工具执行前经 M4 `run_before("tool_call", …)` 挂点（挂点在即可）
- [ ] **整轮 record → 零 token replay**：一次"LLM 调 echo → 拿结果 → final"的 turn 逐字节回放、`inner.calls` 不变、span 轨迹一致
- [ ] TDD：循环终止 / max_iterations 边界 / tool dispatch / 畸形 args 重试 / 工具报错裁决 / replay 一致，各 mutation 可杀
- [ ] 五门全绿（含 lint-imports）；`run_turn`(M1) 与 `assess_once` 空 diff

## Files (owner)
`providers/base.py`(Message/Completion/Provider + tool_calls)、`providers/echo.py`、`providers/replay.py`(录放 tool_calls)、
新 `src/grandquiz/kernel/tools.py`、`kernel/runner.py`(+run_agent_turn)、`kernel/events.py`(+AGENT_TURN/TOOL_CALL EventType)、
新 `tests/test_tool_loop.py`（+ 必要 replay/tools 测试）。

## Blocked by
None（基线 main ae88da9；M4/M6 已在，直接接）。
