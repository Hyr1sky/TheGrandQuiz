# M8-fix③ — 选择题干扰项加硬（prompt + 确定性反-tell 门）

Status: done（merge 至 main 6555fef；四门全绿 252 passed；eval 8/8）
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

## Comments

- 落地：`question_multiple_choice.md` 干扰项段升为硬约束+操作化；`_parse_mc` 并列新增 meta 门 +
  长度离群门（保留 02 去重门 + 既有 grounding/可判卷门）。plausibility 真打分留 Tier 2。
- 终审对抗验证修两处过激（build 门太激进）：meta 门原按 bare 子串 "都对"/"都不对" 匹配 → 误伤
  "两者都对齐"/"指针都不对齐边界"（**MEDIUM**）→ 改按指代性前缀（以上/上述/综上）+ all/none of the
  above 锚定匹配；长度门原双向（正确项独短也挡）→ 误伤"单一术语正解+长干扰"合法形态（**LOW**）→
  改为只查"独长"一向。两处都补了回归测试并用 mutation 实测可杀。
- stem-echo（题干回声）门刻意不做：中文无可靠分词、误报率高——留 Tier 2（brief 已授权此裁剪）。
- prompt 内容哈希 bump（question_multiple_choice）；无 MC golden cassette，故 eval 8/8 仍绿、无 cassette
  回放用例受影响。
