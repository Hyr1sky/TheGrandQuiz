# SE-S5 — 选择题难度杠杆（档位 → 选项数 + judge 验收闸门）

Status: ready-for-agent
Type: AFK

## Parent
[PRD: 自进化第一阶段](../PRD.md)

## What to build

让**选择题**的难度真正随档位变——这是难度落到题面的**硬杠杆腿**（尽量确定性）。两个机制：
①档位越高、干扰项越多；②复用 Tier-2 干扰项 judge（`judge.py`）当验收闸门，档位越高要求干扰项
越难辨，不达标就重生成。

## 锁定设计（不留给实现猜）

- **读档**：出题前 `tier = difficulty.tier_of(item_id)`（S1 台账）。难度台账透传进出题路径
  （`assess_once` → `generate_multiple_choice`；沿 S3 的注入链）。
- **杠杆 ①：档位 → 目标选项数**：确定性映射（拟 1 档 3 选项 … 5 档 6 选项，具体表实现定并写清）。
  `MultipleChoiceQuestion.options` 本就是不定长列表——**不改 schema**。出题 prompt 传入"生成 N 个
  选项（1 正确 + N-1 干扰）"。
- **杠杆 ②：judge 验收闸门**：出题后调 `judge_distractor`（`judge.py`，`role="basic"`）判每个/整批
  干扰项的 `DistractorLabel`。**档位决定验收门槛**（拟：高档要求全部达"合理干扰"、低档容忍"较弱
  干扰"；门槛表实现定）。不达标 → **重新生成**（有界重试，照出题槽现有 `ModelRetry` / `max_attempts`
  模子），重试用尽 → 降级（拟：接受当前最好的一版，或回落更低档要求——实现定，**不炸整轮考核**）。
  这个"生成 → judge → 不够格重生成"闭环是本 issue 的核心。
- **判官不新开角色**：judge 复用 `role="basic"`（PRD 决策 4）；`Role` 枚举**不改**。
- **事件/可观测**：judge 调用作为 model span 挂在出题子树下（照 `judge.py` 现有 span 发射）；
  重生成轮次可观测（trace 里能看到 judge 判了几次、重生成几次）。
- **LLM 槽 → Record/Replay**：judge 与重生成都要有 cassette；新增 record 脚本
  （照 `scripts/record_judge_distractor.py`）。

## Acceptance criteria

- [ ] 出题读 S1 难度档；档位 → 目标选项数确定性映射（不改 MC schema）
- [ ] 高档出题请求更多选项（外部可断言：生成的 options 数随档位增）
- [ ] judge 验收闸门：不达标触发重生成；重试用尽走明确降级、**不炸考核**
- [ ] judge 复用 `role="basic"`，`providers/base.py` 的 `Role` 未改
- [ ] Record/Replay：MC 出题 + judge + 重生成有 cassette；replay 零 token 复现
- [ ] eval harness 用例断言"高档 → 更多选项 / judge 闸门生效"
- [ ] `difficulty=None`（无台账）时出题行为等价改动前（向后兼容）
- [ ] 五门 + eval harness 全绿

## Files (owner, 可能漂)
`domain/learning/assessment/question.py`(读档 + 选项数 + judge 闸门 + 重生成闭环)、
`domain/learning/assessment/engine.py`(透传 difficulty 进出题)、`domain/learning/judge.py`(若门槛
逻辑复用其判定)、`tests/test_mc_distractors.py`、新 replay 测试 + `scripts/record_*`。

## Blocked by
SE-S1（台账，读档）、SE-S3（难度已会写台账，否则读到的恒为默认档、验不出杠杆）。
