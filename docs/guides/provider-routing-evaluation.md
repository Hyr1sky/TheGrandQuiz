# Provider 路由离线评测指南

PCP-07A 交付的是“比较路由策略的尺子”，不是已经上线的自动路由器。它对任意文本任务适用，不认识
LearningResource、题型、判卷或摘要规则，也不会发送网络请求。

## 模块边界

```text
外部预计算结果 ──Dataset Reader──> RoutingDataset
                                         │
调用前请求事实 ──RoutingPolicy───────────┤
                                         ▼
                              RoutingEvaluationReport
```

- `grandquiz.evals.routing_dataset` 读取公开 LLMRouterBench 的逐候选 JSON 结果，只保留 prompt、score、
  token 和 cost；prediction、raw output 与 ground truth 不进入通用数据集。
- `grandquiz.evals.routing` 冻结配对 outcome、来源分组、development／holdout、候选集合和内容哈希，并运行
  固定候选、种子随机或调用者实现的策略。
- 策略只能看到 `RoutingRequest` 与已冻结的候选 ID，看不到候选输出、真实质量、实际成本或实际延迟。
  这些都是调用后事实，拿来选模会造成数据泄漏。
- `analysis:quality-oracle` 事后选择每个 case 的最高质量候选，只量理论上限，标记为 `analysis_only`，不实现
  `RoutingPolicy`。

协议 Adapter 与 Dataset Reader 是两种不同角色。前者把统一 Model 调用翻译成 OpenAI／Anthropic wire
格式并正规化响应；后者只在离线环境导入第三方结果。Reader 不持有 API Key，不执行 retry/fallback，
也不属于生产请求链。

## 数据不变量

每个 case 必须包含完全相同的候选集合。同一 `source_group_id` 不能同时出现在 development 和 holdout，
避免近重复来源泄漏。数据内容哈希不受 case 或 outcome 顺序影响；来源 revision、成本单位和候选集合也进入
哈希。

执行失败与质量差不是一回事：失败 outcome 不允许带语义质量，但成本和 token 可以是已知或未知。报告用
`known_*_count` 明确分母；没有观测时总成本、均值和分位数均为 `None`，不会伪装成 0。

LLMRouterBench 文件只提供整份文件的 `time_taken`，没有每个 case 的延迟。Reader 不把总时间平均摊到每条
记录，而是把逐 case latency 保持为 unknown。以后项目内 Trace 投影若有真实逐请求延迟，可直接填入同一
通用 outcome。

## 最小使用方式

调用者读取两个或更多逐模型 JSON 文件，把路径携带的 dataset、split、候选和来源 revision 显式传给
`LLMRouterBenchResultDocument`，再调用 `read_llmrouterbench_results()`。得到的 `RoutingDataset` 可交给
`evaluate_routing_policies()`：

```python
report = evaluate_routing_policies(
    dataset,
    partition="holdout",
    policies=(
        FixedCandidatePolicy(policy_id="always-a@v1", candidate_id="candidate-a"),
        SeededRandomPolicy(policy_id="uniform-42@v1", seed=42),
    ),
    include_quality_oracle=True,
)
```

`policy_id` 是人可读版本，`policy_fingerprint` 冻结实际配置。自定义策略也必须提供稳定的 64 位十六进制
指纹，且只能使用调用前特征。

## 这份证据能证明什么

公开预计算数据可以证明评测管道没有配对错位、分区泄漏、随机不可重放或未知值冒充零，也能比较固定候选、
随机和 Oracle 上限。它不能证明 DeepSeek、百炼或任一项目 Profile 在 TheGrandQuiz 真实请求上的优劣，
也不能据此开放 `auto`。

PCP-07B 的进入条件是：一个真实消费者、两个显式授权且能力可比的 Profile、版本化质量 rubric、项目内配对
数据，以及足够大的 Oracle gap。先做可解释规则；只有冻结 holdout 上稳定胜过规则基线，才讨论学习型路由。
之后仍须经过 shadow 与小流量 canary，显式 pin、能力门、总 retry/fallback 预算和流式重放安全始终优先。
