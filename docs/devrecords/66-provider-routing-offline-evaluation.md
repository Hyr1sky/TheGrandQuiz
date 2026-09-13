# Provider 路由离线评测基座

日期：2026-09-13。Provider 控制面 PCP-07A；独立 `codex/provider-control-plane` worktree。

## 交付行为

新增 Provider-neutral 的 `RoutingDataset` 与 `RoutingEvaluationReport`。每个 case 冻结调用前请求、
source group、development／holdout 分区和完整候选 outcome；数据内容、来源 revision、成本单位与候选集合
共同形成稳定 SHA-256。case 或 outcome 排序变化不会改写同一逻辑快照。

待评 `RoutingPolicy` 只接收调用前可见的 `RoutingRequest` 与已授权候选 ID，不接收候选输出、实际分数、
实际成本或实际延迟。内置固定候选和无可变全局状态的种子随机基线；质量 Oracle 单独标记为
`analysis_only`，读取事后 outcome 估计理论上限，但不实现生产策略接口。策略的人类版本和配置指纹同时进入
报告。

报告分别保留完成／失败、质量观测、已知成本、输入／输出 token、逐请求延迟、候选选择和失败类别。
Provider/runtime failure 不能携带语义质量；其 usage 和成本可以未知。所有 unknown 都保持 `None` 并附真实
观测分母，不会被补成零或混入一个不透明总分。

## 公开数据 Reader

新增 LLMRouterBench 结果 Reader，按官方 `results/bench/<dataset>/<split>/<model>/<timestamp>.json`
文档字段配对逐候选记录。Reader 校验候选覆盖、record index、prompt、partition 和记录数量完全一致，只导入
prompt、score、token 与 cost；prediction、raw output 和 ground truth 不进入通用路由数据。

官方文件只有整包 `time_taken`，没有逐请求 latency，因此 Reader 明确保留 per-case latency unknown，
不做平均摊派。测试 fixture 按官方公开 schema 独立构造；没有下载完整公开数据包、复制第三方输出、联网调用
模型或使用用户材料。

这是 Dataset Reader／Importer，不是厂商协议 Adapter。实现位于 `evals/`，`providers/`、`kernel/`、
Runner、OpenAI-compatible 和 Anthropic Messages 均无新增引用。

## 文档清洁

- `summarization` 降为未来真实消费者示例，不再作为 PCP-07A 或 Provider 核心前提；
- Always-fast／Always-quality 改为对每个候选的固定基线，避免按名字猜性能；
- 明确协议 Adapter、Dataset Reader、Router、fallback、cascade 与 Oracle 的不同职责；
- 新增面试说明到本地 gitignored `docs/resume/`，不把私人简历材料发布进仓库；
- PCP-07B～D 的进入条件继续阻挡生产规则路由、学习型路由、shadow 和 canary。

## 验收

| 检查 | 结果 |
| --- | --- |
| PCP-07A 专项 | 6 passed |
| Python 全量 pytest | 1391 passed |
| Ruff lint / format | 通过；312 files formatted |
| Pyright strict | 0 errors / 0 warnings |
| Import Linter | 1 kept / 0 broken |
| 离线 Eval harness | 17/17 passed |
| Web Vitest | 93 passed / 13 files |
| Web lint / typecheck / OpenAPI drift | 通过 |
| Web production/package build | 通过；4861 modules transformed，Python static 无漂移 |
| Sites adapter tests | 4 passed |
| Playwright | 29 passed / 1 既有 mobile voice skip（干净进程复跑） |
| sdist / wheel | 构建成功；两个 routing 模块均进入 wheel |

## 未获授权的后续

本次没有新增 `auto` 配置、生产 RoutePolicy、AgentEvent 决策事件、第二 Profile、付费配对采集或厂商间数据
复制。PCP-07B 至少需要一个真实消费者、两个显式授权且能力可比的 Profile、版本化质量 rubric、来源隔离的
项目内配对数据，以及相对最佳固定候选有实际意义的 Oracle gap。若这些证据不成立，正确结果是继续使用
当前静态用途绑定，而不是继续铺设智能路由框架。
