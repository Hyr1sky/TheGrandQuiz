# 38. 紧凑 Claims 与聚焦复核真实原型

日期：2026-08-06

## 一句话结果

紧凑输出成功降低 Token，并在首阶段命中 4/4 个预注册高影响语义目标；但 5 次聚焦复核没有修复任何
错误，反而新增一个假阳性，使逐点准确率从 37/43 降到 36/43、三值一致从 9/12 降到 8/12。
预注册实验失败，Required Claims 不进入默认判卷路径。

## 为什么做这次实验

上一轮 Required Claims 虽然让结构输出 12/12 合法，却要求模型为每条 claim 和 point 重复生成理由，
Token 比 flat baseline 高 50.68%，语义指标也没有改善。我们因此冻结了一个更窄的问题：

1. 第一阶段只返回 claim 状态和答案/材料 Evidence ID；
2. 代码用固定 all-of 推导 point，再聚合三值；
3. 只有把某个 missing point 升级后会改变整题三值时，才允许一次独立复核；
4. 每份答案最多一次、全局最多六次，复核只能升级，不能降级。

这不是“多叫一个模型投票”。它是一条由代码选择、预算有硬上限、影响范围被限制的语义复核 seam。

## 真实结果

同一批 12 条已揭盲 Development Gold、48 个 point、93 条 claims 经 owner rubric audit 后，有 43 个
aligned points 可用于同口径比较。

| 指标 | 紧凑首阶段 | 聚焦复核后 |
| --- | ---: | ---: |
| aligned point accuracy | 37/43（86.05%） | 36/43（83.72%） |
| aligned verdict agreement | 9/12（75.00%） | 8/12（66.67%） |
| 高影响目标 | 4/4 | 4/4 |
| serious FN / FP | 0 / 0 | 0 / 0 |

共调用 DeepSeek V4 Pro / Thinking Off 17 次：12 次紧凑判卷、5 次聚焦复核，结构重试为 0。总 Token
18,561，比 flat baseline 19,512 少 4.87%，也远低于上一轮 29,400。live 与离线 replay 完全一致。

## 为什么仍然失败

成本与结构可靠性都通过了，但语义单调性失败。四个高影响目标已经由紧凑首阶段全部解决；5 次复核中
4 次没有改变结果，唯一一次改变把 H01 `role_perspective` 从正确的 missing 错升为 matched，使样本从
正确的“勉强”变成错误的“对”。

这揭示了一个重要边界：

- Evidence ID 能防止模型伪造或改写证据；
- 代码 all-of 能保证聚合过程确定；
- 有界选择能限制调用次数和爆炸半径；
- 但第二次 LLM 判断仍不能证明“这段原文蕴含该 claim”。单向升级甚至天然偏向 false positive。

所以“可验证结构”不等于“可靠语义”。如果只因为第二个调用更聚焦就相信它，我们只是把同一个不确定判断
做了两遍。

## 决策

按照调用前冻结的退出条件：

1. Required Claims 保留为实验与审计 seam，不作为默认生产判卷策略；
2. 不增加第三层 Judge，不放宽阈值，也不为这条路线消耗新的 unseen human holdout；
3. 下一次代码收口先把新题生成与默认判卷恢复为 flat atomic ExpectedPoint + AnswerEvidenceUnit ID；
   claim-aware 分支只保留兼容与实验入口；
4. 后续产品开发若需要自动判卷，只能从更高质量的人类 rubric、显式不确定性/人工复核或不同任务边界
   重新立项，不能把本实验描述成已通过。

详细 preregistration、owner audit、逐样本报告、cassette 与哈希保存在 gitignored 的
`localtemp/calibration/grading-required-claims-prototype-01/`。
