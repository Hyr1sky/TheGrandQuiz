# Provider 显式选模与能力预检

日期：2026-09-07。Provider 控制面第三竖切（PCP-03），基线 `96f9ec9`；独立
`codex/provider-control-plane` worktree，本地提交前验收。

## 交付行为

`model-config.v1` 在不破坏旧配置的前提下增加 fast／quality 预设和四项能力事实：tools、原生 streaming、
structured output、reasoning。每项明确区分 supported、unsupported 与 unknown；这些值由配置声明，
不根据厂商或模型名猜测。具体 Profile 与预设选择互斥，未知 ID 或能力不足在网络调用前以安全错误失败。

Web Chat 是首个按 turn 选择的真实消费者：创建 session 时只返回本地安全的 Profile ID、预设与能力摘要；
消息请求可携带一次选择，服务端在创建后台任务前解析并冻结 Model，响应返回脱敏执行身份。Chat 要求
tools 与原生 streaming；unknown 和 unsupported 分别解释，不用 completion 模拟流冒充原生能力。

CLI ReAct 增加互斥的 `--model-profile`／`--model-preset`，在会话装配时冻结一次。Runner 可接收本轮已
解析 Model，并在整个 tool-calling 循环复用；原状态机、工具执行、流终结校验、取消与错误传播逻辑未改。
旧 Web/CLI 调用不传选择时仍走默认用途绑定。

## 边界与观测

显式 Chat 选择只影响该 turn；出题、判卷、材料读取、摘要与 Eval 仍按各自 purpose 绑定，不会被连带
覆盖。选择来源加入既有 `model-identity.v1`，因此 `model.started`、Trace、安全诊断、Replay v3 与 Eval
Subject 继续使用同一冻结事实。设置与 Trace 投影扩展有限枚举，不引入当前配置倒填历史。

[ADR-0014](../adr/0014-explicit-model-selection-and-capability-gating.md) 固化了运行输入、能力保守策略与
显式 pin 不静默替换的决策；[配置指南](../guides/model-profiles.md)、TOML 样例、README 与简历面试材料
同步更新。

## 本地验收

| 检查 | 实际结果 |
| --- | --- |
| Python 全量 pytest | 1304 passed |
| PCP-03 聚焦回归 | 147 passed |
| Ruff lint / format | 通过 |
| Pyright strict | 0 errors / 0 warnings |
| import-linter | 1 kept / 0 broken |
| 离线 Eval | 17/17 |
| Web Vitest | 91 passed，13 个文件（单 worker 全量） |
| Web 新增 Chat 选择与既有 Chat 回归 | 20 passed |
| Web lint / typecheck | 通过 |
| OpenAPI / TypeScript schema | 已重新生成 |
| Web build:package / 静态资产 | 通过；生产资产已同步 |
| Sites adapter | 4 passed |

默认并发 Vitest 两次分别出现文章 Markdown、侧栏或 Chat lazy Markdown 的等待超时；所有失败用例单独
复跑通过；新增安全错误展示用例后以单 worker 全量 91/91 复验，未发现 Provider 行为回归，也未扩大本票
去修改无关页面。

## 非目标

没有实现自动路由、应用 retry、Retry-After、fallback 或 Anthropic Messages Adapter；fast／quality
只是确定性别名，不宣称已经通过性能或质量 Eval。没有修改选择题／简答题命题、判卷、题型选择、学习
记账或 prompt 算法。这是路线图内、与命题业务解耦的独立支撑轨，不改变 composite/exploratory 的
产品优先级。
