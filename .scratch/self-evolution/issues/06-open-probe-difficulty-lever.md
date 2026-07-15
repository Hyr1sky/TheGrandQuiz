# SE-S6 — 开放/追问难度杠杆（提示词按档位 + 证据条选择，软腿如实标注）

Status: done（merge 至 main `6815de8`，六门全绿 681 passed / eval 14/14）。`difficulty_prompt_hint(tier)`
（4/5→逼深提示、1/2→放缓提示、3→None，三档而非五串——软杠杆无法断言"真更难"、五串是假精度）
+ `generate_question` 加 `difficulty_hint` 参（append-pattern，照 `_append_asked_before`，None 不追加）。
**关键手法（对抗审查改定，与 issue 原文"加 {{DIFFICULTY_HINT}} 哨兵"相反）**：用追加消息而非改 prompt
文件——open/probe cassette（case6/8/10/13）prompt hash 未变、零重录。**证据条选择（杠杆②）跳过、留后续**。
软性如实标注：测试只断言"不同档追加不同 hint / 默认+None 不追加"，不断言"真更难"。
Type: AFK

## Parent
[PRD: 自进化第一阶段](../PRD.md)

## What to build

让**开放题/追问**的难度也随档位变——这是难度落到题面的**软杠杆腿**。没有像 MC"选项数"那样干净
的结构性杠杆，主要靠**提示词（few-shot）按档位给不同难度提示**；可选的部分确定性辅助是"高档引用
更冷门的证据条"。**本 issue 如实承认这条比 SE-S5 软、断言更粗，不假装确定性对称**。

## 锁定设计（不留给实现猜）

- **读档**：同 S5，出题前 `tier = difficulty.tier_of(item_id)`，透传进开放/追问出题路径。
- **杠杆 ①（主）：提示词按档位分级**：`question_generate.md` / `question_probe.md` 加入难度维度——
  按 `tier` 注入不同的难度指令（拟：低档问核心定义、高档问边界条件/反例/跨概念联系）。可用
  few-shot 示例锚定各档难度的问法。机制照现有 prompt 的 `{{LANGUAGE}}` 哨兵替换（**不用
  str.format**，模板含 JSON 花括号）——加一个 `{{DIFFICULTY_HINT}}` 之类哨兵，代码按档位填。
- **杠杆 ②（可选辅助，确定性）：证据条选择**：`KnowledgeItem.evidence` 是多条证据的列表。高档
  出题时**优先选更不常被引用的证据条**（深挖冷门细节，可判定）。"常被引"的口径 v1 可简单
  （拟：按证据在 item 里的顺序/长度做确定性排序选取，或结合 asked_questions 里已问过的角度避开
  热门条——实现定并写清）。**这条比 ①更实验性，若难落地可降级为纯 prompt 提示，不强求**。
- **软性如实标注**：本 issue 的验收**断言比 S5 粗**——只断言"不同档位确实走了不同 prompt 变体
  （版本/哨兵填充不同）/ 引了不同证据条"，**不断言**"高档题真的更难"（那是主观的、超出确定性
  可断言范围）。PRD 决策 4 已授权这个不对称，issue 里再次写明，避免自欺。
- **LLM 槽 → Record/Replay**：改 prompt → cassette 内容 hash 变 → 重录相关 golden cassette
  （case6/case8/case10/case13 等开放/追问用例）。记录哪些重录、断言行为符合预期变化。

## Acceptance criteria

- [ ] 出题读 S1 难度档；开放/追问 prompt 按档位注入不同难度指令（哨兵替换，非 str.format）
- [ ] 外部可断言：不同档位 → 不同 prompt 变体（哨兵填充内容随档位变）
- [ ] （若实现）证据条选择：高档优先引更冷门证据条，确定性可复现
- [ ] **软性如实标注**：验收只断言"变体不同/证据条不同"，不断言"真的更难"——issue/代码注释写明
- [ ] Record/Replay：受影响开放/追问 cassette 重录，replay 零 token 复现，记录重录清单
- [ ] `difficulty=None` 时行为等价改动前（向后兼容，缺省不填难度哨兵或填标准档）
- [ ] 五门 + eval harness 全绿

## Files (owner, 可能漂)
`domain/learning/assessment/question.py`(读档 + 哨兵填充 + 证据条选择)、
`domain/learning/prompts/question_generate.md`、`domain/learning/prompts/question_probe.md`、
`domain/learning/assessment/engine.py`(透传)、`tests/*`、受影响 cassette 重录 + `scripts/record_*`。

## Blocked by
SE-S1（台账）、SE-S3（难度会写台账）。可在 S5 之后或与之并行（改不同题型路径，冲突面小）。
