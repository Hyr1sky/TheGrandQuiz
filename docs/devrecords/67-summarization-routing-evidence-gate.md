# Summarization 路由证据门

日期：2026-09-16。Provider 控制面 PCP-07B；独立 `codex/provider-control-plane` worktree。

## 结论先行

PCP-07B 已按 **no-go** 收口：不实现智能路由。27 个 development case 的历史实验中，固定 DeepSeek
平均质量为 `0.949074`，analysis-only 质量 Oracle 为 `0.956790`，绝对增益只有 `0.007716`；Oracle 只在
2 个 case 改选 Qwen。该观察不足以支持阈值规则、学习型 Router 或生产 shadow/canary 的复杂度。

提交前复审发现，首轮获批计划虽然冻结了 Profile 配置、单次尝试上限和禁用 fallback，却没有在**审批前**
冻结完整 `ModelIdentity.policy_fingerprint` 与重试策略的 deadline、退避、抖动和总等待预算。因此这批结果降级
为历史探索观察，不作为正式 Eval gate 或可复现的 `EvalSubjectSnapshotV2` 证据；没有用当前配置事后补写事实，
也没有借旧 Yes 审批重建新计划。新的契约已 fail closed，未来只有重新生成完整计划并获得新的 Yes 才能调用。

这不是失败的功能开发，而是预先约定的 Eval gate 正常工作：互补性不足时保留用途到 Profile 的静态绑定，
不从小样本中硬拟合一个“智能”决策器。选择题、简答题命题、判卷、Learning Memory 与 Runner 状态机均未
改动。

## 数据与人工门

- 首个消费者：`summarization`；质量 rubric 为 `summarization_quality@v1`；
- 候选：显式授权的 DeepSeek 与百炼 Qwen Profile；
- 同输入配对生成：37 case，development 27、holdout 10，74 次调用全部完成；
- 双 Judge：只处理 development，匿名候选且交换 A/B 顺序，54 次调用全部返回合法结构；
- 自动 HITL 包：全部 7 个 Judge 分歧加确定性抽取的 5 个一致结果，共 12 case；
- 人工只对完整审查包回复一次 Yes，批准记录绑定哈希
  `831bcc9c11bf7f7ef0048072b3eb8b770f2fa7c43c2ae7750fcd6d2d2d60b5b5`；
- 累计真实调用为 107,890 tokens，低于批准的 600,000 上限；批准后没有新增 Provider 调用。

10 个 holdout 虽已在最初获批的配对生成中产生候选结果，但未进入 Judge、标签、阈值选择或本轮结论。

## 审批后物化边界

新增 `summarization_routing_evidence`，只在下列条件全部成立时把消费者证据转换为通用
`RoutingDataset`：pilot、collection、judge plan、judgements 与 review pack 的来源链可复现；审批为 Yes；
审批哈希精确指向该 review pack；pilot 与 judge plan 在审批前冻结完整模型身份和精确重试策略，执行模型必须
逐项匹配。缺失 runtime、不同 policy fingerprint、不同 retry deadline／等待预算、No、错哈希或被修改的中间
产物都 fail closed。

通用数据只暴露调用前事实：消息数、轮数、用户／助手字符数与输入 UTF-8 字节数。候选输出不进入 Router
接口；候选的评分、token 和延迟只作为事后 outcome。双 Judge 的四项 1～4 分按候选聚合为 8～32 分，再以
`(raw - 8) / 24` 归一化到 `[0, 1]`。本轮没有冻结价格表，所以 cost 明确保留 unknown。

提交前审查进一步要求复用正式 `EvalSubjectSnapshotV2`，而不是合成 model policy fingerprint。新计划会为两个
生成候选和两个 Judge 分别冻结真实 model identity、完整 retry policy、prompt、rubric、token budget、禁用
fallback、workflow 与 harness 版本；四个 subject ID 才能进入 `RoutingDataset.source_revisions`。旧数据哈希
`90c36b73ef646cd4157cdeea044ef8ee8919d20f99423b8d1e810fe9e8459f5d` 已退役，不是当前契约下的门禁证据。

## 历史探索结果（非门禁）

| 策略 | 平均质量 | 总 token | P50 延迟 | P95 延迟 | 选择分布 |
| --- | ---: | ---: | ---: | ---: | --- |
| 固定 DeepSeek | 0.949074 | 18,873 | 1,009.94 ms | 1,593.18 ms | DeepSeek 27 |
| 固定 Qwen | 0.766975 | 18,837 | 1,307.89 ms | 1,970.90 ms | Qwen 27 |
| 种子随机 42 | 0.861111 | 18,815 | 1,039.05 ms | 1,970.90 ms | DeepSeek 14 / Qwen 13 |
| 质量 Oracle（仅分析） | 0.956790 | 18,876 | 1,009.94 ms | 1,692.46 ms | DeepSeek 25 / Qwen 2 |

Oracle 相对固定 DeepSeek 的质量增益约为 0.81%，且依赖生产时不可获得的事后分数。即使只把它当探索观察，
development 中那 2 个 Qwen 胜出样本也不足以支持可泛化阈值；加上被测系统身份未完整预先冻结，本轮不会搜索
规则、打开 holdout 或创建生产 Router。

## 后续重开条件

只有候选模型版本、summarization prompt／rubric、真实请求分布或价格关系发生实质变化，并重新批准新的
配对 pilot 时才重开 PCP-07B。新证据仍须先证明有实际意义的 Oracle gap；否则 PCP-07C 与 PCP-07D 保持关闭。

## 验收

| 检查 | 结果 |
| --- | --- |
| summarization pilot／judge／routing 与 Provider transport 专项 | 复审契约专项通过 |
| Python 全量 pytest | 1403 passed |
| Ruff lint／format | 通过；321 files formatted |
| Pyright strict | 0 errors / 0 warnings |
| Import Linter | 1 kept / 0 broken |
| sdist / wheel | 构建成功；summarization pilot、judge、evidence 模块与 prompt 均进入 wheel |
