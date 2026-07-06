# M8-fix③ — 选择题干扰项加硬（prompt + 确定性反-tell 门）

Status: ready-for-agent
Type: AFK

> 修真机 dogfood 暴露的"干扰项太弱、一眼可排除"。plausibility 的真打分是 Tier 2（LLM-judge，二期），
> 本条 AFK 可验的是确定性反-tell 门 + prompt 加硬。与 01/02/04 可并行。

## Parent

[PRD: M8 Eval Harness + 它护住的 dogfood 质量修复](../PRD.md)

## What to build

把选择题干扰项从"送分"提升为有迷惑性，让选择题能区分"看过觉得懂"和"真懂"。

- 重写选择题 prompt 的干扰项段：plausibility 由软约束升为**硬约束并操作化**——每个干扰项须是**具体常见误解**或**从 item 的概念 / 摘要 / 证据取的邻近但错**概念；所有选项在长度 / 具体度 / 语法上**平行**；禁 meta 选项（"以上都对 / 都不对"）、禁题干回声。
- 出题结构化门（`_parse_mc`）加**便宜的确定性反-tell 门**（长度离群 / 题干词汇回声 / meta 选项）作为 ModelRetry 触发——**只挡表面泄漏，不测 plausibility**。
- 干扰项 plausibility 的真打分（"不懂概念能否排除"的 LLM-judge）显式留给 Tier 2，不在本 issue。

## Acceptance criteria

- [ ] MC prompt 干扰项段为硬约束且操作化（具体误解 / 邻近但错、选项平行、禁 meta / 题干回声）
- [ ] `_parse_mc` 对长度离群 / 题干回声 / meta 选项 raise ModelRetry（缝-3）
- [ ] prompt 内容哈希 bump，旧 MC 相关 cassette 大声失效（重录属人机边界）
- [ ] 现有 case 3 / case 8（假 provider 驱动）仍绿
- [ ] 四门全绿
- [ ] 明确记录：干扰项 plausibility 的 LLM-judge 打分留 Tier 2（二期），不在本 issue 验收

## Blocked by

None - can start immediately
