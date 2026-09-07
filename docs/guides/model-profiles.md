# 模型 Profiles 与用途绑定

TheGrandQuiz 的模型调用分成四步：

```text
业务声明用途 → 控制面选定 Profile → Adapter 翻译厂商协议 → AgentEvent 记录实际身份
```

业务代码只说“这是出题”或“这是判卷”，不选择 DeepSeek、Qwen 或具体 URL。配置模块把用途绑定到
一个冻结的模型；OpenAI-compatible Adapter 只负责消息、工具、流式输出和错误的协议翻译。

## 最简配置

不设置 `GRANDQUIZ_MODEL_CONFIG` 时，现有 `LLM_*` 环境变量会经明确的 legacy importer 转成用途绑定。
只有一套默认配置即可覆盖全部产品用途；完整的 `ENRICH_LLM_*` 仍只为旧用户保留，并映射到出题用途。

需要显式 Profile 时，复制 [模型配置样例](../../model-profiles.example.toml)，在 `.env` 中设置：

```dotenv
GRANDQUIZ_MODEL_CONFIG=/absolute/path/to/model-profiles.toml
LLM_API_KEY=...
DASHSCOPE_MODEL_KEY=...
```

一旦指定文件，文件就是唯一配置来源，不会与 `LLM_*` 或 `ENRICH_LLM_*` 拼接。修改文件后需重启
进程；本阶段不支持热更新。

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
运行期间不会因设置变化或失败而静默换模型。旧客户端不传选择仍走用途绑定的默认 Profile，以保持兼容。

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
`purpose_overrides`、`presets`、`retry`。Connection 管网络协议和凭证引用，Profile 管模型、有效请求
参数与显式能力声明；retry 是所有用途共享的传输恢复上限，不属于某个厂商。

- 本阶段只支持 `openai_chat_completions` wire API。
- 凭证只写环境变量名，真实值仍放在 gitignored 的 `.env`。
- 未知字段、未知用途、缺失引用、非法 URL、URL 鉴权/query、缺失凭证和不支持的参数组合都会在请求前失败。
- 用途覆盖优先于默认 Profile；显式选择只影响被选择的对话运行，不连带覆盖出题、判卷或 Eval。
- Chat 显式选择要求 `tools` 与 `native_streaming` 均为 supported；unsupported 与 unknown 分开报错，
  completion 模拟流不会冒充原生流。
- 不存在隐式模型路由或自动 fallback。
- 生产 Model Runtime 由应用统一管理传输重试，OpenAI SDK 的隐藏重试固定关闭。默认一次逻辑调用总计
  最多 3 次请求（包含首次），总期限 90 秒、累计等待最多 30 秒；限流、连接、超时、冲突和 5xx 仅在
  可安全重放且预算充足时重试。鉴权、权限、坏请求、永久额度和未知错误不会默认重试。
- 可选 `[retry]` 能配置 `enabled`、`max_attempts`、`deadline_seconds`、`base_delay_seconds`、
  `max_delay_seconds`、`max_total_wait_seconds` 和 `jitter_ratio`；未知或越界值会在联网前拒绝。
- `Retry-After` 支持秒数与 HTTP 日期。流已收到任何输出或上游 chunk 时不自动重放；取消立即传播。

## 执行身份与历史

每次 `model.started` 事件记录安全的 `model-identity.v1`：用途、选择来源（默认、用途覆盖、具体 Profile
或预设）、配置指纹和策略指纹。
指纹能区分 endpoint path、wire API、模型和有效参数，但不含 Profile 名、URL、模型原文或凭证引用。
密钥轮换不会改变语义身份。

设置页展示当前绑定；历史 Trace 和诊断包只读取调用当时的事件。当前配置从 A 改成 B 后，A 的历史
仍显示 A 的指纹；旧 Trace 没有身份时显示 `unknown`，不会用 B 倒填。

新版 `model-cassette.v4` 把执行身份、messages 和工具契约共同计入回放键，并保存 typed
failure/success attempt 序列。用途、配置或工具变化都会明确 miss；v3 成功录制继续只读兼容，旧 v1/v2
cassette 只经 legacy reader 读取，新键 miss 不会偷偷回退旧键。
