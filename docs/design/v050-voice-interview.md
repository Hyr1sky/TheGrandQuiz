# v0.5 Voice Interview 设计契约

> 状态：Implemented；paired-audio 词表质量门、真实 Provider dogfood 与 Web 设置收口均已完成
>
> 原型证据：[ASR 工程基础调研](../research/v050-asr-engineering-foundation.md)
>
> 架构决定：[ADR-0012](../adr/0012-voice-transcript-is-reviewable-input.md)

## 1. 目标

v0.5 为桌面 Web 的逐题考核增加一种新的作答输入方式：用户录完一段语音，系统生成可编辑转写草稿，用户确认后
再进入既有判卷 workflow。它同时建立未来模拟面试所需的 Provider、状态、事件和专业词识别基础，但不在本版
实现实时双工考官。

这是一条加厚现有应用的竖切，不是平行的语音产品：

```text
获批 ResourceRevision
  -> RecognitionLexicon（可重建投影）
  -> 当前题目选择 TranscriptionHints
  -> 浏览器录音
  -> VoiceRun / SpeechRecognitionProvider
  -> reviewable transcript
  -> 用户确认或修改
  -> 既有 Assessment answer submission
  -> 既有判卷、申诉、LearningFactJournal 与 Memory
```

## 2. v0.5 范围

### In scope

- 桌面 Chromium 的 `audio/webm;codecs=opus` 完整录音上传。
- `qwen-audio-3.0-asr-flash` 同步 HTTP Adapter。
- 按获批材料 revision 构建小型 RecognitionLexicon，并按当前题目筛选有界 TranscriptionHints。
- 持久、幂等、可取消、可查询的 VoiceRun；服务重启后状态可解释。
- 用户审查/编辑 transcript 后，通过唯一 Assessment seam 提交语音答案。
- 安全错误映射、AgentEvent span、Provider Attempt 审计和 Record/Replay 测试。
- Web 录音、状态、重试、取消、转写草稿审查与提交的完整桌面交互。

### Non-goals

- 实时 WebSocket interim transcript、流式输入、AOQ、Filetrans 或断点续传。
- WAV/MP3 转码、Safari/Firefox/移动端兼容承诺。
- TTS 播题、实时打断、双工对话、数字人形象或 Interview subagent。
- 自动清理口头禅、LLM 改写、语义补全或自动提交。
- 全局个人发音词典、词表人工管理 UI、跨材料概念图谱。
- 静默保存音频、从生产录音自动构造 Eval 数据集或用 WER/CER 宣称普适精度。
- CLI 麦克风录音。CLI 保留调试与审计职责，不复制浏览器采集能力。

## 3. 模块与 seam

保持现有 `kernel / providers / domain / interfaces / evals` 分层，不新建顶层 ASR 子系统。

| Module | Seam / Interface | Owns | Does not own |
| --- | --- | --- | --- |
| Recognition Lexicon | `build_lexicon(revision_id)`、`select_hints(item_ids)` | 术语派生、去重、排序、有界选择、来源追溯 | Provider 参数、音频、判卷 |
| Speech Recognition | `transcribe(request) -> result` | Provider 中立请求/结果、外部错误分类 | Assessment 状态、用户确认 |
| DashScope Adapter | `SpeechRecognitionProvider` adapter | 百炼鉴权、Data URL、响应解析、错误映射 | 业务重试、VoiceRun 状态 |
| Speech Replay Adapter | 同一 interface | fixture key、离线确定性响应 | 生产网络调用 |
| VoiceRun | `start/get/cancel/retry/submit` | 幂等、状态机、attempt、TTL、迟到结果隔离 | 出题、判卷、Learning Memory |
| Assessment | 既有 `submit_answer` | 正式答案、判卷、申诉、记账 | 录音与 ASR 草稿 |
| Web Capture | 浏览器 UI 状态 | 权限、MediaRecorder、Blob、回放、上传 | Provider Key、领域状态 |

建议保持当前代码树的扁平真实边界：

```text
src/grandquiz/
├── domain/learning/recognition_lexicon.py
├── providers/speech.py
├── providers/dashscope_speech.py
├── providers/speech_replay.py
└── interfaces/api/voice_runs.py

web/src/features/assessment-workspace/
├── VoiceAnswerControl.tsx
└── assessment-panel.css
```

只有在真实共同修改历史证明多个文件共同闭合后才继续拆子包。原来架构图里的 `interfaces/asr/` 不落地：浏览器
是输入 channel，外部 ASR 是 Provider adapter，把两者塞进一个目录会混淆两种 seam。

## 4. Recognition Lexicon 契约

### 4.1 权威边界

`ResourceRevision / DocumentNode / KnowledgeItem / approved TagAssignment` 是来源事实；
RecognitionLexicon 是 revision 级、可删除重建的本地投影。它不向 KnowledgeItem 反写 ASR 字段。

```text
RecognitionLexiconV1
  schema_version
  lexicon_id                 # canonical payload 的内容哈希
  revision_id
  builder_version
  entries[]

RecognitionLexiconEntryV1
  entry_id                   # revision + normalized_term + source refs 的稳定哈希
  term                       # 发送给识别器的规范表面形式
  normalized_term            # 去重键，不替代显示形式
  source_kind                # knowledge_item | heading | code_identifier | approved_tag
  source_refs[]              # item_id / node_id / assignment_id
  priority                   # 1..5，Provider 中立
```

v0.5 构建顺序：

1. 优先读取 KnowledgeItem 概念名、代码标识符、结构标题和 approved tag；
2. 规范化 Unicode、空白和大小写用于去重，但保留 `ReAct` 等原始显示形式；
3. 同一术语合并来源并取最高 priority；
4. 拒绝空串、长句、纯数字、低信号常用词和超过长度上限的候选；
5. 生成不可变、内容寻址的 lexicon snapshot。

首版不额外调用 LLM 抽词。若后续事实证明确定性来源召回不足，LLM 只能生成 candidate，且必须记录
builder/prompt/model 版本并通过质量 gate，不能直接成为长期事实。

### 4.2 单次选择

```text
TranscriptionHintsV1
  schema_version
  hint_set_id                # canonical selection 的内容哈希
  lexicon_ids[]
  item_ids[]                 # v0.5 恰好一个；保留多 item 形状
  selector_version
  entries[]                  # term + priority + source entry_id
```

选择器只接收 Assessment 当前已冻结的 `item_id`，优先该 item、其 Evidence 节点和同 revision 高优先级术语；
输出去重后最多 50 项。Provider Adapter 可以把 priority 映射成厂商 weight，但不能改变选择集合。

术语增强可能把未说出的热词错误插入 transcript，进而影响判卷。因此默认启用前必须对同一固定音频做
with/without hints 对照：目标术语改善，同时不得新增“音频未出现、只因词表出现”的术语。用户仍必须审查草稿，
Trace 记录 hint_set_id 与条目数量，但不记录原始术语文本。

## 5. Speech Recognition Provider 契约

```text
TranscriptionRequestV1
  audio_bytes
  mime_type
  locale_hint?
  hints: TranscriptionHintsV1
  timeout_seconds

TranscriptionResultV1
  transcript
  provider_request_id?
  provider_audio_duration_ms?
  latency_ms
```

公共结果不承诺 word timestamps、confidence、emotion 或 Provider raw response。Adapter 可以在本地调试日志中
观察额外字段，但任何新公共字段必须先有产品消费者和跨 Provider 语义。

稳定错误类别：

```text
invalid_audio | unsupported_media | payload_too_large
provider_auth | provider_rate_limited | provider_timeout
provider_unavailable
```

创建 VoiceRun 之后的持久错误向 Web 暴露 `code / stage / reason / retryable`；尚未创建 VoiceRun 的 HTTP
边界失败复用全局安全信封 `code / message / retryable / trace_id`。两者都不暴露 API Key、完整 Provider body
或堆栈。

## 6. 两个状态机

### 6.1 浏览器 CaptureSession（不进入后端领域模型）

```text
idle -> requesting_permission -> recording -> uploading -> reviewing
  \             \                \           \             \
   cancelled     failed           failed      failed         failed
```

“结束录音并识别”是一次明确的用户动作：离开录音态后停止全部 `MediaStreamTrack` 并立即上传完整 Blob；本版
不增加一个上传前的本地确认阶段。上传后保留本地回放，转写只进入可编辑草稿，仍需再次确认才会成为正式答案。
浏览器刷新会丢失尚未上传的 Blob，这是 v0.5 的明确限制。

### 6.2 服务端 VoiceRun（持久应用状态）

```text
accepted -> transcribing -> reviewable -> submitted
    |             |             |
    +---------- cancelled <-----+
    |             |
    +---------- failed
                  |
                  +-- explicit retry --> transcribing

reviewable -- TTL --> expired
```

终态为 `submitted / cancelled / expired`；`failed` 只有在 `retryable=true` 且 attempt 未耗尽时允许显式重试。
服务启动时遗留的 `accepted/transcribing` 统一收敛为 `failed(code=service_restarted)`，不能永远显示运行中。
若 `reviewable` 草稿对应的内存 AssessmentSession 已随服务重启丢失，则立即过期并擦除 transcript；v0.5 不承诺
跨进程恢复提交。

```text
VoiceRunV1
  schema_version
  voice_run_id
  request_id                    # 一次用户操作的幂等键
  assessment_session_id
  question_id, item_id
  status, version
  mime_type, byte_count, client_duration_ms, audio_sha256
  hint_set_id, hint_count
  provider_attempt_count
  reviewable_transcript?        # 本地短期字段；终态/TTL 后清除
  retryable, error_code?
  created_at, updated_at, expires_at
```

同一 `request_id` + 相同音频/题目返回原 VoiceRun；同一 request_id 携不同内容返回 conflict。每次外部调用产生
新的 `provider_attempt_id`，最多两次，且只由用户显式触发；不做盲目自动重试。

## 7. 提交、取消与数据保留

- `VoiceRun.start` 必须绑定当前 `assessment_session_id + question_id + item_id`；换题后旧 run 不能提交。
- `VoiceRun.submit(edited_text)` 在一个应用命令中调用既有 Assessment submission，固定
  `input_modality=voice`；Assessment 成功后 VoiceRun 才进入 submitted。
- transcript 可以完全由用户改写；系统保存最终答案，不能宣称它仍是“ASR 原文”。
- accepted/transcribing 取消后立即阻止状态晋升；Provider 迟到响应只更新 attempt 审计，不能恢复草稿。
- 上传尚未返回 `voice_run_id` 时，客户端用 request ID 写入取消 tombstone；随后到达的 start 必须 fail closed，
  避免孤儿 VoiceRun。
- 原始音频不落磁盘、不进 Trace、不进 LearningFactJournal；请求结束后释放内存。
- reviewable transcript 仅在本地保存 30 分钟；submitted/cancelled/expired 后清除。长期只保留既有 final answer、
  `input_modality=voice` 和必要的运行引用。

## 8. 首版运行限制

| Limit | v0.5 default | Reason |
| --- | --- | --- |
| Browser | desktop Chromium | Prototype 01 已验证的唯一环境 |
| MIME | `audio/webm;codecs=opus` | 已验证直传，无转码 |
| Recording duration | 90 seconds | 面试单题与成本/等待的首版平衡 |
| Raw Blob | 7,000,000 bytes（约 6.7 MiB） | 给 Provider 10 MB JSON/Base64 限制留余量 |
| Provider timeout | 30 seconds | 真实 37 秒样本约 2 秒，保留网络余量 |
| Provider attempts | 2 | 显式重试，避免重复费用 |
| Review TTL | 30 minutes | 同进程短暂恢复；30 秒周期 sweep 主动清理，不长期保存机器草稿 |
| Hint entries | 50 | 保持当前题目小型词表并控制偏置 |

限制必须来自统一后端配置并投影给 Web；前端校验用于即时反馈，后端仍是安全权威。

## 9. AgentEvent 与 Trace

VoiceRun 继续使用唯一事件脊柱：

```text
voice.run.started
  -> voice.provider_attempt.started
  -> voice.provider_attempt.ended
  -> voice.reviewable | error
  -> voice.submitted | voice.cancelled | voice.expired
voice.run.ended
```

`voice.run.started/ended` 共享 span；Provider attempt 是其子 span。显式重试开启同一 trace 内的新 run span，
不能把 attempt 挂到已经失败闭合的父 span 下。Trace 只保存 ID、状态、MIME 类别、字节数、
时延、attempt、hint_set_id/count、Provider request ID 和安全错误，不保存音频、transcript、词条正文或原始响应。
Observatory 继续使用有限 UI event 映射，不能直接暴露内部事件名。

## 10. 验收门

1. 词表从固定 revision 确定性生成；同输入同版本字节等价，revision 更新不覆盖旧 snapshot。
2. 选择器只使用当前 item 范围，最多 50 项；未知 item/revision fail closed。
3. Replay Adapter 覆盖成功、403、429、timeout、畸形响应；普通测试不访问真实 Provider。
4. VoiceRun 幂等、冲突、取消迟到响应、显式重试、重启收敛和 TTL 清理均由确定性测试覆盖。
5. voice submit 只调用一次既有 Assessment submission，并产生 `input_modality=voice` 的 AssessmentAttempt。
6. Web 覆盖权限拒绝、结束并识别、上传后回放、大小/时长限制、上传、取消、错误、编辑草稿和提交。
7. 固定音频对照证明 hints 改善目标术语且不新增未说出的词表术语，才允许默认开启术语增强。
8. 一次显式授权的真实 Provider dogfood 保存脱敏 report/cassette；原始音频默认留在 gitignored 本地目录。
9. Python、Web unit、OpenAPI、Playwright、lint/typecheck/build 全绿，再做 Standards + Spec 双轴审查。
