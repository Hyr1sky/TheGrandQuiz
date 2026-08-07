# 40. 判卷澄清二分类原型

日期：2026-08-06

## 一句话结果

Clarification Signal Prototype 01 的结构和成本通过，但语义门失败：合法输出 12/12、找回 2/5、误追问
1/7、precision 66.67%、9,587 Token；live/replay 逐字节一致。更重要的发现是，判卷 matched/missing Gold
不能直接充当“是否应该向用户追问”的 Interaction Gold。

## 为什么做

纯领域澄清 planner 只接受 `diagnosis=uncertain`，但 30 条 Development Gold 的生产诊断里
`uncertain=0`。本轮没有放宽字符串规则，而是从报告中用代码选出 12 个决定性 missing point：只把其中
一个升级为 matched 就会改变三值。原始 grading Gold 中 5 个是模型 false negative，7 个是真 missing。

原型分类器只输出 `clarify | accept_missing`，引用 AnswerEvidenceUnit ID；它没有改写 point label、verdict
或 Learning Memory 的路径。预注册要求找回至少 4/5、误追问至多 1/7、precision 至少 80%、总 Token
不超过 10,305。

## 结果

| 指标 | 结果 | 门限 |
| --- | ---: | ---: |
| 合法输出 | 12/12 | 12/12 |
| 找回 false negative | 2/5 | ≥ 4/5 |
| 误追问 | 1/7 | ≤ 1/7 |
| precision | 66.67% | ≥ 80% |
| 重试 | 1 | ≤ 1 |
| Token | 9,587 | ≤ 10,305 |

唯一重试是合法 JSON 外包 Markdown code fence，错误回显后恢复；没有 Evidence ID 或字段组合失败。
正确触发 H08/H20；漏掉 H01/H06/H18；触发了 Gold 仍为 missing 的 H16。按预注册规则 `won=false`，
不接生产，也不通过事后改标签翻盘。

## 为什么失败很有价值

逐例审计显示，H06/H18 更像“答案已直接或等价表达、初判冲突”，不应让用户重复证明自己；H16 的
“安全退出、不应吞掉”虽然还不足以命中“向开发者冒泡”，却确实存在适合澄清的两种解释。这说明：

```text
grading label:       matched | missing
interaction label:  direct support | ambiguous support | no support
```

两条轴不能互相推导。二分类原型把“判卷冲突”和“真实表达歧义”压成同一个 clarify，数据契约本身不对。

## 下一道门

先由 owner 审核这 12 条独立 Interaction Gold：

- `no_support` → 接受 missing，不追问；
- `ambiguous_support` → 向用户澄清一次；
- `direct_or_equivalent_support` → 进入 `needs_review`，不自动改判，也不让用户为 Judge 错误买单。

审核完成前不改生产 prompt、不再调用 Provider。三分信号即使在 Development Gold 胜出，仍需单独验证
澄清问题是否非引导、补答后是否收敛，最后才值得收集新的 unseen human holdout。

## Owner 后续裁决

owner 已接受 12 条独立 Interaction Gold：`no_support=6`、`ambiguous_support=2`、
`direct_or_equivalent_support=4`。Support Relationship Prototype 02 已冻结 Gold hash、prompt hash、三态
路由与成功门。后续真实结果见 [三态 Support Relationship 真实原型](41-support-relationship-prototype.md)：
整体 gate 仍失败，它继续只是 gitignored throwaway prototype，不是生产 schema。
