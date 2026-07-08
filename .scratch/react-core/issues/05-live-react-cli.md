# R1-S4 — 真机 ReAct CLI（`grandquiz react` 对话 agent 接进真机通道）

Status: AFK 骨架 done（merge 至 main 10e0a04；五门全绿 357 passed ×3；react --help 确认注册；内核空 diff）；**HITL dogfood 待用户**
Type: AFK 建 + HITL dogfood

> 终审记：run_react 复用现有装配件（register_learning_tools 4 工具 + 文件式 fetch 源 file://local/<name> + ScriptedApprovalGate
> keep-all + 一个 Runner/EventEmitter 贯穿全会话 + TraceStore 独立库）；stdin 逐行会话循环；react_system.md 版本化 prompt（避开
> 深读器/判卷官/出题官 字样防 provider role 分流冲突）；printer +AGENT_TURN/TOOL_CALL 事件全 escape；**_ScopedEmitter 用 __getattr__
> 委托 inner 加固（S2 concern 闭掉）**；三回合会话零 token replay。4 mutation 全杀。非阻塞 concern：SqlitePreferenceMemory 未接
> react（语言仍 task.language，接入要改 next_question/submit_answer 签名）→ 折进 S3。**真机模型 function-calling 是 dogfood 命门。**

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build（AFK 部分）

把 ReAct 对话 agent 接出成真机命令，让整套从 test-harness 变成可跑的对话体验：

- **`grandquiz react` 子命令**（argparse）：`OpenAICompatProvider.from_env()` + 组装 `ToolRegistry`
  （`register_learning_tools`：ingest / query_weak / next_question / submit_answer，注入真依赖 SqliteStore/Memory/
  Preference + provider + **文件式 fetch 源（复用现有 `ingest` 命令那套，读本地材料文件，非 httpx——真 httpx 仍缓办**）
  + approval + quiz_seed）+ **会话循环**（读 stdin 用户消息 → `run_agent_turn` → 打印助手回复；多回合，`_QuizSession`
  待答态跨回合留存）。
- **ReAct system prompt**（版本化 prompt 文件 name@digest）：定义助手角色（学材料→考核的学习助手）+ 何时调哪个工具
  （入库 / 查薄弱 / 出题 / 判卷）+ 不可信内容纪律。
- **落 trace**：`register(TraceStore)` → 独立 trace.db，会话结束打印 trace_id（同现有 quiz/ingest）。
- **Rich 呈现**：复用 / 扩展 `QuizEventPrinter` 处理新事件（AGENT_TURN / TOOL_CALL / QUESTION_ASKED / ANSWER_JUDGED …）——脊柱投影，动态文本 escape。
- **加固 `_ScopedEmitter`**（S2 遗留 concern）：`__getattr__` 委托或组装，去掉 partial-subclass（不调 super().__init__）的脆弱。
- **AFK 冒烟测试**：脚本化 / 回放 provider 驱动整条会话（fake provider 返回 tool_calls + 工具结果）→ 断言"入库→出题→答→判卷"多步轨迹装配跑通、事件流正确、**整会话零 token replay**。**不测真机模型**（那是 dogfood）。

## Acceptance criteria（AFK）
- [ ] `grandquiz react` 命令注册（console script / argparse），from_env provider + ToolRegistry 组装 + 会话循环 + 落独立 trace.db + 打印 trace_id
- [ ] ReAct system prompt 版本化（trace 记版本号）
- [ ] Rich printer 覆盖新事件类型；动态文本 escape（防 markup 注入，沿用旧坑修法）
- [ ] `_ScopedEmitter` 加固（覆盖任意 EventEmitter 方法不再 AttributeError）
- [ ] 冒烟/replay：脚本化多步会话（入库→出题→答→判卷）装配跑通、事件流断言、整会话零 token replay
- [ ] 五门全绿（含 lint-imports）；考官内核 / assess_once 空 diff
- [ ] 粘合层用假件可测，真 tty / 真模型留 dogfood

## 人机边界（HITL，AFK 建完交回）
- 用真 key 跑 `grandquiz react`，dogfood 对话体验：**确认 deepseek/qwen 是否支持 OpenAI 兼容 function-calling**（tool 循环真机能否跑）、对话编排是否顺、交互考核体验、system prompt 品味调优。
- （若真机 function-calling 不理想，是一个真机发现——可能要调 provider/prompt 或换 tool-calling 方案，留待 dogfood 后定。）

## Files (owner)
`interfaces/cli/app.py`（+react 子命令 + 组装）、`interfaces/cli/printer.py`（+新事件）、新 ReAct system prompt（`domain/learning/prompts/` 或 react 专属）、`domain/learning/tools.py`（加固 `_ScopedEmitter`）、新 `tests/test_cli_react.py`。

## Blocked by
[S2b — 交互考核工具](04-interactive-quiz-turn-tools.md)（done）。**不依赖 S3**（run_agent_turn 已有基础上下文，ContextBuilder 是后续增强）。
