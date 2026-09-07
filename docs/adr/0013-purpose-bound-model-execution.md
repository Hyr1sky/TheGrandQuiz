# ADR-0013: 用途绑定的模型执行接口

- 状态：已接受
- 日期：2026-09-07

## 背景

MVP 使用 `basic/enrich` 同时表达业务用途和模型来源：出题固定走 enrich，其余调用大多走 basic。
这个设计能快速接入两家模型，但同一角色后来承载对话、Reader、判卷、总结和 Eval 等不同职责，也让
Runner、领域代码、录放和诊断都知道了部署选择。单模型用户必须理解虚构的双槽，模型路由与历史身份也
无法在同一套契约里准确表达。

ProviderFailure 已统一外部错误，但“为什么选这个模型、实际执行了什么配置”仍缺少冻结事实。

## 决策

采用用途绑定的 `Model` Interface：

1. 业务和 Eval 调用者声明有限用途；composition 在启动时把用途解析为已绑定 Model。
2. Model 的主调用不含 `basic/enrich`；Adapter 接收已确定配置，只翻译 wire API、厂商方言、流和错误。
3. Connection、Profile、用途覆盖与凭证值分离；凭证只在 Runtime 创建连接时由环境引用解析。
4. 每次模型调用把安全执行身份写入既有 AgentEvent；历史投影不读取当前设置倒填。
5. Replay v3 和 Eval Subject v2 纳入执行身份；旧配置、cassette 与 Subject 由显式 legacy 入口保留。

本决策不引入自动路由、fallback、应用重试或 Anthropic Messages Adapter。它们必须由后续真实消费者
和独立 ticket 拉动。

## 备选方案

- 继续扩展 `basic/enrich`：接口简单，但每增加一种用途都会继续混合业务职责与部署选择。
- 让每个调用点直接传 model、URL 和厂商参数：配置灵活，但协议知识和敏感信息会扩散到业务层。
- 在 Adapter 内按用途路由：隐藏了选择逻辑，Trace 只能事后猜测，也难以测试和冻结。
- 一次实现通用智能路由与 fallback：超出当前证据，且会提前引入候选、能力、成本和故障策略。

## 后果

业务代码不再关心厂商请求格式；新增协议只需提供同一 Model Interface 的 Adapter。配置错误在网络前
大声失败，厂商错误继续统一为 ProviderFailure。当前设置、历史事实、Replay 和 Eval 使用同一冻结身份。

代价是启动装配需要注册全部用途，新 cassette/Subject 需要版本化，旧数据只能显示不完整身份。用户级
选模、能力预检、retry 与 fallback 仍是后续工作，不能从 Profile 存在推断为已经支持。
