# Provider 用途绑定、Profile 与执行身份

日期：2026-09-07。Provider 控制面第二竖切（PCP-02），基线 `18245d1`；位于独立
`codex/provider-control-plane` worktree，PCP-01 与本票均未提交。

## 交付行为

MVP 的 `basic/enrich` 不再进入新的生产模型调用。八个真实用途被固定为 `chat`、
`question_generation`、`answer_grading`、`distractor_review`、`material_reading`、
`grounded_answer`、`summarization` 与独立 Eval 的 `eval_quality`。业务调用者只声明用途；启动装配把用途
绑定为 role-free `Model`，OpenAI-compatible Adapter 只处理请求、工具、流、连接生命周期与统一错误。

Runner 的普通与流式模型接口均已迁移。变更只移除内部 `role` 选择、改用已绑定 `Model` 并在
`model.started` 写执行身份；原状态机、工具执行、流 terminal 校验、取消分支、`model.ended` 封口与错误
原样传播保持不变。completion-only 模型只有经 composition 明确套用 `CompletionAsStreamModel` 才模拟
一个 delta，预算包装不会伪装原生 streaming 能力。

## 配置与传输

新增 `model-config.v1` TOML：Connection 保存 endpoint、wire API 与凭证环境引用；Profile 保存模型与
有限有效参数；一个默认 Profile 可覆盖全部产品用途，可选 `purpose_overrides` 只改变指定用途。指定
`GRANDQUIZ_MODEL_CONFIG` 后文件是唯一模型配置来源；未指定时，PCP-01 的旧环境配置经显式 importer
迁移，原 enrich→出题、basic→其余用途的分配不变。

解析拒绝未知字段／用途／Profile、非法或含鉴权/query 的 URL、缺失引用与凭证、无效 timeout 和已知
不支持的 dialect 参数组合。所有凭证先验证，再创建传输；Runtime 冻结环境快照并拥有关闭责任。
Adapter 内 OpenAI SDK 的隐藏 retry 设为零：本票不实现应用 retry，但先保证未来 PCP-04 有唯一 owner，
不会在不可观测处重复计费。真实 AsyncOpenAI + 本地 MockTransport 证明一次 503 只发送一次请求。

## 执行身份、观测与回放

`model-identity.v1` 只包含用途、有限选择来源、配置指纹与该用途的策略指纹。配置指纹覆盖规范化 endpoint
path、wire API、模型和有效参数；不含 Profile 名、URL 原文、模型原文或凭证引用／值。密钥轮换和 TOML
顺序不改身份；只覆盖出题模型不会使判卷身份或 Replay key 抖动。

每次真实调用把当时身份放入既有 AgentEvent。安全 Trace 与诊断包从历史事件重建；旧事件显示 unknown，
不会用当前设置倒填。Settings/诊断新增默认空的 additive `model_bindings`，因此旧 v1 数据仍可读取；
Web 设置抽屉展示用途、选择来源和短指纹。新的生产 ModelBindings 不再伪报 basic/enrich 卡片，旧 Provider
假件仍保留原安全视图。

`model-cassette.v3` 以执行身份、messages 与规范化工具契约共同取 key；用途、配置或工具变化明确 miss，
不会回退 v1/v2。新增安装包内 v3 fixture 驱动真实 QualityJudge。`eval-subject.v2` 在保留 prompt、tool、
workflow/policy 身份的基础上纳入冻结 ModelIdentity；v1 类型和既有 subject_id 原样保留，原 17-case 继续
经显式 legacy reader 离线执行。

## TDD 与审查修正

核心契约按红→绿实施：默认／用途覆盖、八个消费者、Runner role-free 调用、预算与 streaming 能力、
零隐藏 SDK retry、历史 A/当前 B、旧 Trace unknown、Replay v3 miss、新 Eval fixture 及安装包资源。
最终审查又以失败测试捕获并修复两项问题：全局绑定表导致无关用途策略指纹抖动；additive 字段被误生成
为 required。设置页也先以组件与 API 测试暴露旧角色误报，再迁移到真实用途绑定视图。

## 本地验收

| 检查 | 实际结果 |
| --- | --- |
| Python 全量 pytest | 1289 passed |
| Ruff lint / format | 通过 |
| Pyright strict | 0 errors / 0 warnings |
| import-linter | 1 kept / 0 broken |
| 离线 Eval | 17/17 |
| Web Vitest | 89 passed，13 个文件 |
| Web lint / typecheck | 通过 |
| OpenAPI / TypeScript schema | 重新生成并做幂等核对 |
| Web build:package / 静态资产 | 通过；新设置抽屉资产已同步 |
| Sites adapter | 4 passed |
| Playwright 桌面与移动端 | 29 passed / 1 既有移动端语音 skip |
| wheel / sdist | 离线构建成功 |
| 安装包 CLI / Eval / 资源 | CLI help、17/17；v3 fixture 与 Web 静态页存在 |

E2E 使用仓库离线 fixture，只监听 loopback；桌面和移动设置场景均看见用途绑定，并验证偏好写入。Browser
插件在本会话不可用，故按仓库 Playwright 流程验证；另做桌面截图检查，布局无截断或覆盖。开发页控制台
有一条未能关联到 response 的通用 404 资源消息，完整 E2E、目标交互、API 与构建均无失败，未据此扩大
本票修复范围。

## 兼容层与非目标

旧 `Provider/Role`、OpenAICompatProvider、BudgetedProvider、cassette v1/v2 与确定性 Eval 假件只在
`legacy.py`、旧 reader、历史资产和明确兼容入口保留。退出条件是旧环境配置、旧 cassette 与依赖 role
断言的历史 Eval 全部完成数据迁移；本票不删除用户资产或伪造 v3 录制。

未实现 PCP-03 至 PCP-07：用户本次选模／flash 与质量预设、Capabilities、应用 retry／Retry-After、
fallback、Anthropic Messages Adapter 与智能路由。没有修改命题、判卷、题型选择、学习记账或 prompt
算法；没有真实厂商网络、付费调用、用户数据库迁移、commit、push 或远程 CI 声明。

这是路线图内、与命题业务解耦的独立支撑轨；不改变 composite/exploratory 的产品优先级。
