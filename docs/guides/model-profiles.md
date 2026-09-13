# 模型 Profiles 与用途绑定

TheGrandQuiz 的模型调用分成四步：

```text
业务声明用途 → 控制面选定 Profile → Adapter 翻译厂商协议 → AgentEvent 记录实际身份
```

业务代码只说“这是出题”或“这是判卷”，不选择 DeepSeek、Qwen 或具体 URL。配置模块把用途绑定到
一个冻结的模型；OpenAI-compatible 与 Anthropic Messages Adapter 只负责各自的消息、工具、流式输出和
错误协议翻译。

## 最简配置

不设置 `GRANDQUIZ_MODEL_CONFIG` 时，现有 `LLM_*` 环境变量会经明确的 legacy importer 转成用途绑定。
只有一套默认配置即可覆盖全部产品用途；完整的 `ENRICH_LLM_*` 仍只为旧用户保留，并映射到出题用途。

需要显式 Profile 时，复制 [模型配置样例](../../model-profiles.example.toml)，在 `.env` 中设置：

```dotenv
GRANDQUIZ_MODEL_CONFIG=/absolute/path/to/model-profiles.toml
LLM_API_KEY=...
DASHSCOPE_MODEL_KEY=...
ANTHROPIC_API_KEY=...
```

一旦指定文件，文件就是唯一配置来源，不会与 `LLM_*` 或 `ENRICH_LLM_*` 拼接。修改文件后需重启
进程；本阶段不支持热更新。

## 模型列表与下拉框

当前 Web 下拉框展示的是配置文件 `[profiles]` 中允许本应用使用的 Profile ID，以及可选的 fast／quality
预设；它不会拿 API Key 调厂商接口自动导入全部模型。只配置 `.env` 的 legacy 入口时没有可选目录，仍由
`LLM_MODEL` 指定唯一模型；需要下拉选择时应启用 `GRANDQUIZ_MODEL_CONFIG` 并在 TOML 中列出 Profile。

这是一条有意保守的边界：厂商“模型列表”通常只能证明名称可见，不能统一证明 tools、原生 streaming、
上下文／输出上限、reasoning、价格或当前账号权限。DeepSeek 的 OpenAI-compatible `/models` 只提供基本
模型身份；百炼另有按地域／Workspace 查询且元数据更丰富的目录接口。未来若加入自动发现，它应先生成
待确认候选，再由用户补齐或确认能力与用途，不能直接变成已授权 fallback／路由集合。

## 显式选择与预设

Profile 可以额外声明 `tools`、`native_streaming`、`structured_output`、`reasoning` 四项能力，
每项取值为 `supported`、`unsupported` 或 `unknown`。这是本地配置事实，不是系统根据模型名猜测；
当用户显式选择模型时，真实消费者会在联网前校验自己需要的能力。

`fast`、`quality` 是可选的确定性别名，只把名称映射到一个 Profile，不代表系统已测得该模型更快或
更优，也不会触发黑盒路由。Web 对话可在每轮发送前选择；CLI ReAct 可在会话开始时固定：

```bash
grandquiz react --model-preset quality
grandquiz react --model-profile writer
```

`--model-profile` 与 `--model-preset` 互斥。Web 每个 turn 冻结一次选择，CLI 整个 ReAct 会话冻结一次；
运行期间不会因设置变化而偷偷换模型。只传 `profile_id`／`preset` 是严格 pin；一次 Web/API 选择只有再
显式提供有序 `fallback_profile_ids`，且本地 fallback policy 已启用，才允许在临时可用性故障后切换。
旧客户端不传选择仍走用途绑定的默认 Profile，以保持兼容。

## 用途

产品运行注册七个用途：

- `chat`：开放对话与工具编排；
- `question_generation`：出题；
- `answer_grading`：判卷；
- `distractor_review`：选择题干扰项评审；
- `material_reading`：材料深读；
- `grounded_answer`：材料问答；
- `summarization`：历史摘要。

`eval_quality` 属于独立 Eval 配置上下文，不受产品用途选择影响。不同用途可以绑定同一个 Profile；
用途不同表示职责不同，不表示必须使用不同厂商。

## 配置规则

配置文件固定为 `model-config.v1`，包含 `connections`、`profiles`、`default_profile` 和可选
`purpose_overrides`、`presets`、`retry`、`fallback` 与 `fallback_candidates`。Connection 管网络协议和
凭证引用，Profile 管模型、有效请求参数、容量与显式能力声明；retry 是所有用途共享的传输恢复上限，
fallback 只管理已授权候选间的恢复，不属于某个厂商。

- `wire_api` 支持 `openai_chat_completions` 与 `anthropic_messages`，必须在 Connection 显式声明，不按域名
  或模型名猜测。
- 凭证只写环境变量名，真实值仍放在 gitignored 的 `.env`。
- 未知字段、未知用途、缺失引用、非法 URL、URL 鉴权/query、缺失凭证和不支持的参数组合都会在请求前失败。
- 用途覆盖优先于默认 Profile；显式选择只影响被选择的对话运行，不连带覆盖出题、判卷或 Eval。
- Chat 显式选择要求 `tools` 与 `native_streaming` 均为 supported；unsupported 与 unknown 分开报错，
  completion 模拟流不会冒充原生流。
- 不存在隐式模型路由。fallback 默认关闭；`fallback_candidates` 按用途只列备用 Profile，主 Profile 仍由
  default／purpose override 决定。列表有序、去重且不能回到主部署；它同时构成允许发送本次材料的范围，
  系统不会因为环境里存在另一个 key 就自动加入候选。
- 启用 fallback 的主备 Profile 必须声明 `context_window_tokens` 与 `max_output_tokens`，备用容量不得低于
  主模型；请求要求的 tools/streaming/structured output/reasoning 和排除的部署身份也会在联网前检查。
  不合格候选不会通过删 Evidence、截断材料或移除工具来迁就。
- 生产 Model Runtime 由应用统一管理传输重试，OpenAI／Anthropic SDK 的隐藏重试固定关闭。默认一次逻辑调用总计
  最多 3 次请求（包含首次），总期限 90 秒、累计等待最多 30 秒；限流、连接、超时、冲突和 5xx 仅在
  可安全重放且预算充足时重试。鉴权、权限、坏请求、永久额度和未知错误不会默认重试。
- 可选 `[retry]` 能配置 `enabled`、`max_attempts`、`deadline_seconds`、`base_delay_seconds`、
  `max_delay_seconds`、`max_total_wait_seconds` 和 `jitter_ratio`；未知或越界值会在联网前拒绝。
- `Retry-After` 支持秒数与 HTTP 日期。流已收到任何输出或上游 chunk 时不自动重放；取消立即传播。
- fallback 默认仅接受 rate limit、timeout、connection 与 server error。鉴权、权限、坏请求、not-found、
  永久额度、拒绝/安全阻断和未知错误不会靠换厂商绕过。同一 Logical Call 的 retry 与 fallback 共用
  `max_attempts` 和 deadline，例如两个候选也不会把 3 次上限扩大成 6 次。
- fallback 只重发当前尚未完成的模型请求，不重跑 workflow、工具或审批。即使没有收到输出，本地也只能
  证明请求满足 replay-safety 条件，不能证明远端没有处理、计费或其他不可见副作用。

最小启用示例（完整字段见仓库根样例）：

```toml
[fallback]
enabled = true
max_attempts_per_candidate = 1

[fallback_candidates]
chat = ["writer"]
```

原生 Anthropic Messages 的最小 Profile：

```toml
[connections.claude]
base_url = "https://api.anthropic.com"
api_key_env = "ANTHROPIC_API_KEY"
wire_api = "anthropic_messages"

[profiles.claude]
connection = "claude"
model = "your-claude-model"
thinking_mode = "disabled"
max_output_tokens = 4096

[profiles.claude.capabilities]
tools = "supported"
native_streaming = "supported"
structured_output = "unknown"
reasoning = "unsupported"
```

Messages API 强制要求输出上限，因此 `max_output_tokens` 在该协议下既是资格容量，也是每次请求实际发送的
`max_tokens`；缺失时启动即失败。首版将前置 system 文本放进独立 system blocks，把 assistant
`tool_use` 与紧邻的 user `tool_result` 保真互译，支持并行 client tool calls。`end_turn`、
`stop_sequence` 与 `tool_use` 是可完成终态；`max_tokens`、上下文超限、`refusal`、`pause_turn`，以及
thinking、引用／多模态、服务端工具、厂商侧 fallback block 都明确失败，不会删掉后伪装普通成功。
对应地，首版 Anthropic Profile 也不能把 `reasoning` 或 `structured_output` 声明为 `supported`；等 Adapter
真正实现且有消费者验收后才能开放该能力事实。

## 执行身份与历史

每次 `model.started` 事件记录安全的 `model-identity.v1`：用途、选择来源（默认、用途覆盖、具体 Profile
或预设）、配置指纹和策略指纹。
指纹能区分 endpoint path、wire API、模型和有效参数，但不含 Profile 名、URL、模型原文或凭证引用。
密钥轮换不会改变语义身份。

设置页展示当前绑定；历史 Trace 和诊断包只读取调用当时的事件。当前配置从 A 改成 B 后，A 的历史
仍显示 A 的指纹；旧 Trace 没有身份时显示 `unknown`，不会用 B 倒填。

新版 `model-cassette.v4` 把执行身份、messages 和工具契约共同计入回放键，并保存 typed
failure/success attempt 序列。候选顺序、fallback policy 与共享 retry policy 进入策略身份；每个实际候选
仍按自己的 ModelIdentity 分键，因此离线回放会复现已冻结的切换而不是重新发现模型。用途、配置或工具变化
都会明确 miss；v3 成功录制继续只读兼容，旧 v1/v2 cassette 只经 legacy reader 读取，新键 miss 不会
偷偷回退旧键。

## 离线路由评测不是生产路由

PCP-07A 的通用数据与基线实现位于 `grandquiz.evals`。它读取公开或消费者投影的预计算候选 outcome，
只把调用前可见的请求文本与通用特征交给待评策略；质量定义仍由数据生产者／真实消费者提供。报告保留
质量、成本、token、延迟和失败的独立维度，未知成本或失败 usage 不会被补成零。

公开结果导入模块称为 Dataset Reader／Importer：它把外部文件变成 `RoutingDataset`，不翻译真实厂商
请求。OpenAI-compatible 与 Anthropic Messages 才是协议 Adapter。固定候选和种子随机是生产候选的
比较基线；质量 Oracle 会读取事后结果，只用于估计理论上限，不能实现或冒充在线 `RoutingPolicy`。

因此当前仍没有 `auto` 选择。进入规则路由至少需要一个真实消费者、两个明确授权且能力可比的 Profile、
版本化质量 rubric、来源隔离的项目内配对数据，以及在未调参数据上胜过固定／随机基线的证据。详见
[Provider 路由离线评测指南](provider-routing-evaluation.md)。
