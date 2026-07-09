# R1-S9 — ReAct 循环对畸形 tool_call 鲁棒（修"神了"会话崩溃）

Status: done（merge 至 main 8d69662；五门全绿 411 passed；内核空 diff；cassette 绿）
Type: AFK

> 终审记：_parse_tool_calls 容错畸形/非对象 JSON（try/except + isinstance dict）→ 裹 sentinel；dispatch 在
> pydantic 校验前认出 sentinel 抛 ModelRetry(DEGRADED) → 复用 M6 回灌路径（runner/recovery 零改）。**关键：
> 显式拦而非只靠 pydantic——query_weak 无必填字段否则把畸形空 dict 当合法静默跑（红测证）。** react 会话循环
> try/except Exception 兜单轮、坏轮打印+continue 不杀整场、失败轮不留孤儿 user 消息、KeyboardInterrupt 仍优雅退出。

> 缘起（dogfood trace 762884ba seq 113-116）：用户输入"神了"（非任务闲聊）→ 模型吐了个 tool_call 但其
> `arguments` 是畸形 JSON（char 22 断）→ `OpenAICompatProvider._parse_tool_calls` 的 `json.loads(arguments)`
> 抛 `JSONDecodeError` 未被接 → 整个 react 会话崩中断。LLM 在非任务输入上乱吐坏 tool_call 是常态，不该崩会话。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build

让 ReAct 循环对 LLM 吐出的**畸形 tool_call** 鲁棒——走 M6 RecoveryPolicy 降级、绝不崩会话：

- **`OpenAICompatProvider._parse_tool_calls`**：`json.loads(function.arguments)` 包 `try/except JSONDecodeError`。畸形参数**不抛裸 JSONDecodeError**——表示成一个"参数非法"的可恢复态：让 dispatch 的 pydantic 校验拒它 → `ModelRetry`（DEGRADED）→ `run_agent_turn` 走 M6 RecoveryPolicy（SKIP：把"工具参数非法，请重试"作 tool 结果回灌）→ LLM 下一轮改对。设计要点：畸形参数与"合法但校验不过"走**同一条 DEGRADED 恢复路径**（复用 S1/S5 已有的 dispatch→ModelRetry→RecoveryPolicy），不新造分支。
- **react 会话循环（app.py `run_react` / `_run_react_cli`）**：单轮 `run_agent_turn` 若仍冒出未预期异常，**兜住这一轮**（打印一行友好提示 + 继续下一轮 / 或干净结束），**不让一轮坏 turn 杀掉整场会话**。KeyboardInterrupt 照旧优雅退出。
- 不新增对记账/replay 的影响；畸形 tool_call 的恢复是确定的（不依赖 clock/random）。

## Acceptance criteria
- [ ] `_parse_tool_calls` 对畸形 arguments JSON 不抛裸异常（单测：mock OpenAI 响应含畸形 arguments → 不崩、产出可被 dispatch 拒的形态）
- [ ] 畸形 / 校验不过的 tool_call → 经 M6 RecoveryPolicy DEGRADED 回灌重试（loop 测：provider 给坏 tool_call → 循环不崩、走恢复 → 后续轮可继续）
- [ ] react 会话循环兜住单轮异常、不整场崩（测：某轮 run_agent_turn 抛 → 会话打印提示并继续 / 干净退出，非 traceback 中断）
- [ ] 既有 tool 循环 / replay / golden cassette 不回归
- [ ] 五门全绿（含 lint-imports）；不碰记账/选题/判卷内核

## Files (owner)
`providers/llm.py`（`_parse_tool_calls` 容错）、必要时 `kernel/runner.py`（畸形→RecoveryPolicy 路由，若非已覆盖）、`interfaces/cli/app.py`（react 会话循环单轮兜底）、`tests/test_llm_provider.py`+`tests/test_tool_loop.py`+`tests/test_cli_react.py`（相应测试）。

## Blocked by
None（main 1cdd002）。#1/#2（KB/考核范围模型）另议，不在本 issue。
