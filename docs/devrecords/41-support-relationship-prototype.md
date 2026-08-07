# 41. 三态 Support Relationship 真实原型

日期：2026-08-06

## 一句话结果

owner 接受独立 Interaction Gold 后，三态原型在 DeepSeek V4 Pro / Thinking Off 上得到合法 11/12、
exact 9/12、`no_support=5/6`、`ambiguous_support=0/2`、`direct_or_equivalent_support=4/4`，3 次重试、
12,342 Token；live/replay 逐字节一致。预注册失败，不接生产。

## 契约

本轮第一次把判卷 Gold 与交互路由彻底拆开：

```text
no_support                   -> accept_missing
ambiguous_support            -> clarify once
direct_or_equivalent_support -> needs_review
```

`needs_review` 只表示初判 missing 与答案 Evidence 冲突；原型没有自动改写 point label、verdict 或
Learning Memory 的路径。12 条 owner-accepted Interaction Gold 分布为 6 / 2 / 4。

## 预注册结果

| 指标 | 结果 | 门限 |
| --- | ---: | ---: |
| 合法输出 | 11/12 | 12/12 |
| exact | 9/12 | ≥ 10/12 |
| no support | 5/6 | ≥ 5/6 |
| ambiguous | 0/2 | 2/2 |
| direct/equivalent | 4/4 | ≥ 3/4 |
| no support → ambiguous | 0 | 0 |
| direct → no support | 0 | ≤ 1 |
| retries | 3 | ≤ 1 |
| Token | 12,342 | ≤ 10,305 |

两次 code fence 分别在 H02/H20 经重试恢复；H16 连续把 `relationship_reason` 写成不存在的
`answer_evidence_reason`，最终结构失败。语义上，H01 ambiguity 被压成 no support；H16 两次原始输出都
倾向 direct support；H13 又把“红变绿”过度推断为隐含重构，产生一个 review false positive。

## 工程判断

结果不是“再补一句 Prompt 就能过”：模型在 4/4 direct support 上表现稳定，却完全没有使用
ambiguous 中间态。继续在只有 2 个 ambiguity positive 的已见 cohort 上调参，会把 Development Gold
变成 few-shot 训练集并产生虚假进步。

因此当前保持 flat Grader、一次性 ClarificationFlow seam 与生产关闭状态。可单独讨论的后续方向是：

1. 把 direct-support conflict 收窄为只会进入 `needs_review` 的 abstention signal；
2. 让用户主动发起“我的答案被误解了”澄清，而不是自动预测歧义；
3. 若仍要自动澄清，先建立独立且正例更充分的 Interaction Dataset，再预注册新实验。

以上都不是本轮已通过或已接线的能力。
