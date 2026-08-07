# Benchmark 规模、acceptance semantics 与 Replay 序列

日期：2026-08-04
阶段：Holdout 03 前置决策完成；新 release gate 尚未运行

## 一句话蓝图

Gold Set 回答“我们有没有可信人工标签”，Holdout 回答“这些标签是否仍对开发过程保密”，Benchmark 则把
数据、切分、模型配置、指标和回放协议组合成完整评测系统。本轮没有急着堆新字段，而是先拒绝了过重的
布尔 rubric，并修复了会让真实重试无法精确回放的 cassette 缺陷。

```text
人工标注数据
   ├─ 已揭盲 / 已用于调参 ──> Development Gold ──> 回归与错误分析
   └─ 未见、冻结 hash ─────> Release Holdout ─────> 只运行一次正式 gate
                                      │ 揭盲后
                                      └──────────────> Development Gold

Benchmark = 数据 + split + Provider/model/thinking/prompt + policy + report + cassette
```

## Holdout 到底需要多大

12 条能发现失败，但不能稳定证明 80%–90% 的质量。若真实准确率约为 80%，样本级 95% Wilson 区间大致是：

| 样本数 | 约 95% 区间 | 适合做什么 |
| ---: | ---: | --- |
| 12 | 52%–94% | 找明显 bug |
| 30 | 63%–91% | 个人项目最小 release gate |
| 50 | 67%–89% | 较稳的版本比较 |
| 100 | 71%–87% | 更适合对外质量声明 |

这里的样本单位是独立答卷，不是评分点。一个题里的多个 point 会共享题干和 rubric 风险，不能把 12 题 ×
4 point 当作 48 个完全独立样本。相同题目的多位答题者也存在相关性，因此还要报告 unique QuestionSpec。

本项目的下一批 Holdout 03 预注册为：30 条 eligible 人类答卷、至少 20 个不同 QuestionSpec、约 120–160
个 ExpectedPoint；允许最多 6 条 rubric 排除缓冲。若 eligible 少于 24，只能报告
`insufficient_evidence`，不能临场降低门槛。

## Gold Set、Holdout 和 Benchmark 的关系

- Gold Set 是“有可信答案”的数据属性；可以是开发集，也可以暂时封存。
- Holdout 是“现在不能看、不能调”的使用状态；第一次结果被分析后就失去 holdout 身份。
- Benchmark 是完整协议；除了 Gold 数据，还必须有切分、防污染、模型身份、指标、成本和可回放证据。

因此“我们有 Gold Set”不等于“我们还有可用 holdout”；“跑过 12 条报告”也不等于已经有成熟 benchmark。

## 为什么没有生产化 nested requirements

我们在 H02/H07/H08/H10 上比较 flat ExpectedPoint 与 nested `all_of/any_of`：

| 指标 | Flat unit-ID baseline | Nested requirements |
| --- | ---: | ---: |
| 最终合法输出 | **4/4** | 3/4 |
| 首轮合法输出 | **4/4** | 2/4 |
| Retry | **0** | 4 |
| Token | **6,275** | 24,019 |

Nested 确实有价值：H02 不再把“安装不是无限授权”脑补成具体 resource scope；H10 不再把“阻断”脑补成
修复指导；H07 也正确用 any_of 表达“强依赖或证据丢失，任一风险即可”。但这恰好说明领域模型会立刻从
all_of 长出 any_of，之后还可能需要 optional、threshold、exception，最终变成一套 Boolean rubric 语言。

H08 更关键：材料说预算可以选择原文、摘要或引用，学习者用过期过滤、最近 N 轮和 Top-K 实现预算控制。
Nested 方案把列举的表示手段变成了全部必答条件，制造新的假阴性；而三次输出又都复制示例里的 `p1/r1`
占位 ID，导致最终无合法判决。

生产决定因此保持简单：

1. 每个 ExpectedPoint 只写一个可独立判断的语义不变量；
2. 独立且都必答的条件拆成多个 flat points；
3. 合理替代方案写成同一个 acceptance boundary，不建立 any_of tree；
4. 参考实现默认只是 example，除非题面明确限定，否则不能变成必答项。

## Replay 为什么也需要序列

真实模型具有随机性。两次 retry 的 messages 可能完全相同，但模型返回不同结果：

```text
same replay key
   ├─ attempt 1 completion：占位 point_id，解析失败
   └─ attempt 2 completion：真实 point_id，解析成功
```

旧 Cassette 是 `key -> one completion`，第二条会覆盖第一条。于是 live H10 是 3 attempts / 8,771 tokens，
离线 replay 却变成 2 attempts；整轮 live 24,019 tokens，replay 只有 21,566。最终标签碰巧相同，但运行过程
和成本已经不再可复现。

修复后，新 cassette 允许 `key -> ordered completions[]`，ReplayProvider 每次调用消费下一条，耗尽后大声
抛 `ReplayMiss`。旧 `key -> completion` 文件继续按原语义重复返回，因此既有 fixture 不需要重录；
`reuse_existing=True` 仍读取首条，保持“有录制就不再付费”的行为。

## 验证与下一步

- 同 key 两条不同 text/usage 经 save/load 后按顺序回放，第三次明确报序列耗尽；
- legacy 单条 cassette 重复回放保持兼容；
- 标准开放题与追问共用的出题契约都已锁定 flat rubric authoring：一个 point 一个语义不变量，独立必答
  条件拆点，替代实现/同义表达/示例默认留在同一接受边界，禁止生成 `all_of` / `any_of`；
- 完整工程门在本记录完成后统一运行。

下一步仍不应马上运行 Holdout 03。先按同一规则人工审查新 QuestionSpec：题干是否真的要求每个评分点、
示例有没有被误升格、critical point 是否确实决定核心方向。审题通过后，再收集人类答卷、冻结标签并只
运行一次正式 gate。这里刻意不写一个根据“和/或”等词猜测语义原子性的程序 linter；那类启发式会把新的
误报伪装成确定性质量门。
