"""M8 Eval Harness——把 10 条考核竖切 eval 用例跑在同一条事件脊柱上，规则 scorer 断言 + 报告。

（含 case9 语言一致性 / case10 去重回归探针；即 8 个既有用例 + 这 2 条新探针。）

**只兑现 Tier-1 规则断言**：``graders/`` 里按 case id 键控的确定性 Python scorer（读事件流 /
result / 记忆 / 存储 / span 树五族）。**Tier-2 LLM judge 仍待建（scoped-out）**——本 harness 当前
不含任何 LLM 评审槽，别把它读成已双 Tier。

借 inspect_ai 的 Task / Solver / Scorer / 报告**词汇与形状**（reference-map.md:48），但保留手写
runtime、不引入 inspect_ai 依赖：

- ``cases/*.yaml``：每个用例的输入 / 前置 + case id + 期望的**有序事件类型序列**（字符串列表）。
- ``graders/``：按 case id 键控的规则 scorer（读事件流 / result / 记忆 / 存储 / span 树五族）。
- ``harness.py``：``Solver`` 通用适配器（从 case 重建确定性前置、调既有入口一次、捕获事件 +
  trace）+ runner + 报告（per-case pass/fail、token 成本列、prompt 版本 name@digest）。

用与现有单题 / ingest 测试相同的**假 provider（canned JSON）**驱动，独立于 cassette、不录制、
不触网。``ReplayMiss`` 等 provider 基础设施异常在 runner 里**硬失败**，绝不静默通过。
"""
