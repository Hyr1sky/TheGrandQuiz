"""M8 Eval Harness——把 17 条考核竖切 eval 用例跑在同一条事件脊柱上，规则 scorer 断言 + 报告。

（8 个既有用例 + case9 语言一致性 / case10 去重回归探针 + GKB-S7 的 case11 scope-honor /
case12 empty_scope / case13 题型 honor 三条全局 KB 探针 + 两条 react 层用例：case14
大批量出题不能编造、case15 自然材料问答必须有界检索并精确引用 + case16 Web Acquisition
规范化回放与质量失败零 KB 污染 + case17 真实 ReAct search → 用户选择 → ingest 决策回放。）

Tier-1 是 ``graders/`` 里按 case id 键控的确定性 Python scorer（读事件流 / result / 记忆 /
存储 / span 树五族）。Tier-2 是 eval 层校准优先的 ``QualityJudge``：首版只给 case15 声明
``grounded_answer`` profile，默认从真实 cassette 离线 Replay，独立统计 judge trace 与成本；其余
16 条用例继续只跑 Tier-1。

借 inspect_ai 的 Task / Solver / Scorer / 报告**词汇与形状**（reference-map.md:48），但保留手写
runtime、不引入 inspect_ai 依赖：

- ``cases/*.yaml``：每个用例的输入 / 前置 + case id + 期望的**有序事件类型序列**（字符串列表）。
- ``graders/``：按 case id 键控的规则 scorer（读事件流 / result / 记忆 / 存储 / span 树五族）。
- ``solvers.py``：按 ``ingest / assess / react`` 重建确定性前置并捕获统一 ``SolveResult``；ReAct
  决策必须使用真录 cassette 回放，不能用恒定正确的假 provider 代替。
- ``runner.py``：组合 Tier-1、校准优先 Tier-2 与硬失败语义，产出 ``CaseReport``。
- ``reporting.py``：只消费 ``CaseReport``，输出文本和自包含 HTML。
- ``harness.py``：稳定 facade；CLI、录制脚本与既有测试无需理解上述实现拆分。

``ReplayMiss`` 等 provider 基础设施异常在 runner 里**硬失败**，绝不静默通过。
"""
