# Provider DeepSeek 实机冒烟验收

日期：2026-09-11。Provider 控制面 PCP-01～06 已提交并推送后，在独立
`codex/provider-control-plane` worktree 使用操作者本地、被 gitignore 的临时配置完成最小实机验收。

## 验收范围

本次只验证 Provider 控制面的配置闭合与 OpenAI-compatible Adapter 的普通／原生流式链路。测试发送两段
固定短文本，不读取或上传学习材料、题目、答案、Trace、数据库或其他用户数据；记录不包含 API Key、凭证
环境变量名、endpoint、模型名、厂商响应正文或 request id。

配置解析得到一个可选 `shared` Profile 与 `fast` 预设，七个产品用途均完成绑定；该 Profile 明确声明
tools、native streaming 支持，structured output 未知，reasoning 不支持。下拉菜单读取的正是这份经过
安全投影的 Profile／Preset 目录，而不是从 Key 猜测或从厂商目录自动授权模型。

## 实机结果

| 检查 | 结果 |
| --- | --- |
| 配置与凭证预检 | 通过；所有用途在分配传输前闭合 |
| 普通 completion | 成功；输出非空；prompt/completion/total 为 21/8/29 tokens |
| 原生 streaming | 成功；10 个文本增量；唯一 CompletionFinished |
| 流完整性 | 增量拼接与 terminal 文本一致 |
| 流 usage | prompt/completion/total 为 21/10/31 tokens |
| 敏感数据边界 | 未记录 Key、endpoint、模型名或响应正文 |

这证明“本地 Profile 选择 → 用途绑定 → OpenAI-compatible 传输 → 普通／流式归一化”可以在真实 DeepSeek
服务上闭合。它不证明工具调用、限流重试、fallback、Anthropic Adapter 或 Web 浏览器交互已在真实厂商网络
上执行；这些能力仍由既有确定性测试、官方协议 fixture 与本地 MockTransport 覆盖。

## 与 PCP-07 的边界

本次是 conformance smoke，不是智能路由质量数据。它只有一个候选、两条不同测试请求，没有同请求的候选
配对输出、盲评标签、价格快照或来源隔离 Holdout，因此不能据此训练或晋升路由策略。PCP-07 仍须按票面
先确定一个用途和两个可比 Profile，再单独批准成对付费实验。

这是路线图内、与命题业务解耦的独立支撑轨；不改变 composite/exploratory 的产品优先级，也未修改选择题、
简答题命题、判卷、Evidence 或学习记账逻辑。
