# Holdout 03：30 条真实人类答卷的正式 Release Gate

日期：2026-08-04
状态：正式 gate 已运行并失败；数据已揭盲，降级为 Development Gold

## 这次验证了什么

前两轮开发集和合成挑战只能证明“已知错误能被修复”，不能证明 Grader 面对新答案仍然稳定。本轮先冻结：

- 20 个全新 QuestionSpec；
- 30 条独立、闭卷、未使用 AI 的人类答案；
- 120 个 ExpectedPoint 人工标签；
- 题目、答卷、annotation、Compilation 和隐私审核后的 Dataset Snapshot hash。

Dataset Snapshot 为
`71a504b0725e41e9992e217de1daf89429f1b126faaa281c7d8822558d306743`，包含 30 eligible / 0
exploratory。运行前没有看生产 Grader 输出，也没有根据模型结果修改人工标签。

固定执行条件是 DeepSeek V4 Pro、Thinking Off、`answer_grade@d94c1942`。真实调用录制 31 个响应：29 题
一次成功，H09 首次材料引文漏掉 Markdown 反引号，被 exact-citation 契约拒绝后重试成功。最终 30/30
输出合法，随后离线 replay 的全部逐点决定、Token 和指标与 live 完全一致。

## 结果

| 指标 | 实际 | 门槛 | 判断 |
|---|---:|---:|---|
| Eligible human responses | 30 | ≥ 30 | 通过 |
| Valid output rate | 100% | 100% | 通过 |
| Point accuracy | 90.83%（109/120） | ≥ 90% | 通过 |
| Verdict agreement | 83.33%（25/30） | ≥ 85% | **失败** |
| Serious false negative | 0 | 0 | 通过 |
| Serious false positive | 0 | 0 | 通过 |

总 Token 为 51,528（prompt 40,345 / completion 11,183），平均每条 1,717.6。30 条规模下，三值一致率
必须至少 26/30 才能跨过 85%；本次只差一题，但不能把 83.33% 四舍五入成通过，也不能临时降低阈值。

## 五个分歧为什么出现

五题都只漏了一个 ExpectedPoint，没有 false positive：

- H01：答案写了“查接口为什么慢”和具体排查步骤，模型因整体采用 Skill 视角而漏掉 task action；
- H06：答案分别写出迁移流程固定/局部可判断、审查框架固定/具体判断开放，模型没有组合成局部自由度拆分；
- H08：OCR 仍只在确认扫描件后触发，模型却把额外 PyMuPDF 检查误读为违反参考路径；
- H18：“当前状态快照 + 新会话以摘要为起点”表达了检查点恢复，模型只接受更直接术语；
- H20：模块规则按路径触发加载等价于 path-scoped rules，模型因文件名和术语不同判 missing。

逐条检查 AnswerEvidenceUnit 后，支持内容都完整存在，模型也可以选择多个 ID。因此主要问题不是 Evidence
切分、结构契约或代码聚合，而是语义 entailment 的召回不足：组合表达、机制等价和非参考实现名仍可能被
过度保守地判 missing。

## 工程判断

这次 gate 的价值恰好在于没有“为了发布而通过”：

1. Snapshot、provenance、exact Evidence、重试和 replay 链路全部工作正常；
2. 结构可靠性已经达到 100%，说明上一轮 Evidence ID 改造有效；
3. 质量短板从“输出会坏”收窄为“语义召回还差一点”；
4. 三值聚合只是忠实放大逐点 false negative，不应通过改聚合隐藏问题；
5. Holdout 03 已揭盲，任何后续调优只能把它当 Development Gold，发布仍需新的未见 Holdout。

下一步应先用这五条固定反例做窄版 contrastive entailment prototype：目标至少修复 4/5 false negative、
不新增 false positive、结构合法率保持 100%，Token 增幅不超过 15%。在实验胜出前不直接改生产 Prompt；
实验胜出后仍必须重新收集新的未见人类答案才能打开 gate。
