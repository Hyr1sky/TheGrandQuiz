# R1-S5 — 真 provider function-calling 接线（修 dogfood 发现的空心）

Status: AFK done（merge 至 main 96a2b2f；五门全绿 366 passed ×3；golden cassette 16 passed；replay_key 未改）；**HITL 再 dogfood 待用户确认真机 function-calling**
Type: AFK 建（mock 测）+ HITL 再 dogfood 确认真机

> 终审记：ToolSpec(providers/base)+ ToolRegistry.tool_specs()(pydantic model_json_schema、按名排序保确定)；complete 加
> tools=None 向后兼容（openai 2.x omit 哨兵保纯文本请求逐字节不变）；映射 assistant tool_calls / role=tool、解析
> response.tool_calls(JSON 串→dict)；run_agent_turn 传 tool_specs + 显式 role=basic 进 payload。4 mutation 全杀。
> 非阻塞 concern：加宽 Provider 协议 + strict pyright 覆盖 tests → 18 测试替身 + harness.py 机械补 tools: object=None
> （行为不变、逐处 verified），协议改动必然涟漪。真机 function-calling 是 dogfood 命门，待用户确认。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## Why（dogfood 真机发现）
`OpenAICompatProvider.complete()`（llm.py:78-102）**完全不发 `tools` 参数、也不解析 `tool_calls`**——模型不知道能调工具，
只能用文本"扮演"调用（真机 trace `dac2ef`：11 个 agent_turn、**0 个 tool_call**、ingest/出题/判卷全没真跑）。
AFK 测试用假 provider（按剧本返回 tool_calls）照不到这块。本 issue 把真 provider 的 function-calling 接通。
ReAct 编排走 basic=deepseek（已确认），deepseek OpenAI 兼容端点支持 function-calling。

## 锁定设计
- **`ToolSpec`（providers/base.py）**：generic `{name, description, parameters(JSON schema dict)}`。`ToolRegistry.tool_specs()`（kernel/tools.py）从每个 Tool 的 pydantic 入参模型 `model_json_schema()` 生成 ToolSpec 列表（kernel→providers 是既有合法依赖，runner 已 import providers.base）。
- **`Provider.complete` 加 `tools: Sequence[ToolSpec] | None = None`**（默认 None → 向后兼容，既有调用方/测试不受影响）。echo / record / replay 透传或忽略该参数。
- **`OpenAICompatProvider.complete`**：
  - tools 非空 → 映射成 OpenAI `tools=[{type:"function", function:{name, description, parameters}}]` 传给 create()。
  - 消息映射：assistant 带 `tool_calls` → OpenAI `{role:"assistant", tool_calls:[{id, type:"function", function:{name, arguments: JSON字符串}}]}`（内部 dict → JSON 字符串在边界转）；`role="tool"` 结果 → `{role:"tool", tool_call_id, content}`。
  - 解析 `response.choices[0].message.tool_calls` → `Completion.tool_calls`（arguments JSON 字符串 → dict）；无 tool_calls 则同旧取 `.content` 文本。
- **`run_agent_turn`**：把 `registry.tool_specs()` 传给 `provider.complete(tools=...)`；ReAct 生成用**显式 role="basic"** 并**记进 model.started payload**（修 trace 里 role 为空）。
- **replay_key 不变**（tools 不进键；一条轨迹里 registry 固定、同 messages 同 tools）——保既有 golden cassette 命中。record/replay 照录/放返回的 Completion（含 tool_calls），S1 已支持。
- enrich=qwen 的出题是普通 completion（非 function-calling），**不在本 issue**；prompted-tool-calling 兜底**不做**（先走原生，dogfood 不稳再议）。

## Acceptance criteria
- [ ] `ToolSpec` + `ToolRegistry.tool_specs()`（pydantic → JSON schema）
- [ ] `Provider.complete` 加 `tools=None`；echo/record/replay 透传不破坏；既有纯文本调用/测试全绿
- [ ] `OpenAICompatProvider`：发 tools（mock 断言 create() 收到正确 OpenAI function 格式）+ 解析 tool_calls（mock 返回带 tool_calls 的响应 → Completion.tool_calls 正确，arguments dict）+ 映射 assistant tool_calls / role="tool" 消息
- [ ] `run_agent_turn` 传 tool_specs + 显式 role=basic 进 payload
- [ ] 既有 golden cassette（assess/reader replay）仍绿（replay_key 未变）
- [ ] mock 单测（沿用 test_llm_provider.py 套路，mock AsyncOpenAI，**不触真网**）
- [ ] 五门全绿（含 lint-imports；kernel↛domain）

## 人机边界（AFK 建完交回）
真机再 dogfood `grandquiz react`：确认 deepseek 真发结构化 tool_calls、工具真 fire（trace 里出现 tool_call + question_asked + item_created）。若真机 function-calling 不稳 → plan B（prompted tool-calling）。

## Files (owner)
`providers/base.py`（ToolSpec + complete 签名）、`providers/llm.py`（发 tools + 解析 + 消息映射）、`providers/echo.py`/`replay.py`（透传 tools 参数）、`kernel/tools.py`（tool_specs()）、`kernel/runner.py`（传 tools + 显式 role）、`tests/test_llm_provider.py`（+function-calling mock 测试，+必要 runner/tools 测试）。

## Blocked by
[S4 — react CLI 骨架](05-live-react-cli.md)（done）。修完再 dogfood，然后 S3。
