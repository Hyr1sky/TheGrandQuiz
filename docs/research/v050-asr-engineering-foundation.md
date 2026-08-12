# v0.5 Voice Interview：ASR 工程基础调研

> 状态：研究记录，不是 PRD、ADR 或最终架构
>
> 调研日期：2026-08-07
> 资料边界：浏览器标准、Provider 官方文档与一手开源源码；不比较价格，不决定最终 Provider，不修改代码

## 1. 研究问题

本记录回答五个窄问题：

1. 浏览器怎样取得麦克风并产出可上传的音频？
2. “上传一段录音”和“实时流式识别”在协议与任务模型上有什么区别？
3. 取消、重试、幂等、断点续传分别发生在哪一层？
4. 主流 ASR Provider 实际返回什么，哪些 metadata 不能假设一定存在？
5. 成熟开源项目怎样组织“录音 → 转写 → 确认/发送”？

本记录刻意不回答：

- v0.5 最终选哪个 Provider；
- 最终目录树、领域实体和 API schema；
- ASR 价格、模型质量排名；
- TTS、实时双工对话或数字人方案。

## 2. 先给结论

以下是资料直接支持、且会影响后续设计讨论的结论。

1. **ASR 不总是“上传音频，拿到一个 JSON”。** 同一产品可能提供完整 JSON、纯文本、字幕、SSE
   transcript events、双向流式 interim/final events 或异步 task/operation。
2. **输出流式不等于输入流式。** 完整文件先上传、服务端再用 SSE 返回文字 delta，仍属于上传式识别；麦克风音频持续分片送入 WebSocket/gRPC，才是实时输入流。
3. **浏览器不能保证固定录音格式。** 应通过 `MediaRecorder.isTypeSupported()` 探测，并以
   `MediaRecorder.mimeType` / `Blob.type` 记录实际容器与 codec。
4. **`timeslice` 不是天然的断点续传协议。** 标准明确允许单个录音 Blob chunk 无法独立播放，只保证完整录音
   的所有 Blob 组合后可播放。
5. **停止 UI、停止浏览器上传和取消 Provider 计算是三件事。** `AbortController` 能终止浏览器 fetch，不能据此
   推断远端计算已停止；长任务 Provider 若有取消 API，也可能只是 best effort。
6. **POST 转写不能盲目自动重试。** HTTP 标准不保证 POST 幂等；没有 Provider 明示的幂等契约时，应用必须防止
   一次用户操作产生重复调用、重复费用或两个竞争结果。
7. **转写文本在用户确认前不是正式答案。** 成熟项目通常先把它放入可编辑草稿；Open WebUI 甚至把自动发送做成
   显式设置。TheGrandQuiz 已有 `input_modality=voice`，但它适合标记用户最终确认并提交的答案，不适合把
   未确认 ASR 猜测直接提升为学习事实。
8. **第一轮实验的主要风险不是判卷，而是 codec、权限、资源释放、取消竞态和专业词转写。** 这些都应该先通过
   小型 prototype 获取证据，再决定产品限制。

## 3. 当前项目已经具备的接缝

这是本地代码事实，不是本轮新增设计：

- `AnswerSubmissionMetadata.input_modality` 已限定为 `text | voice`；
- FastAPI 的 `AnswerSubmissionRequest` 已接受同一字段；
- `AssessmentAttempt` 历史投影会保存该字段；
- Web 生成的 OpenAPI schema 已包含 `voice`；
- 当前 Web 仍固定以 `input_modality: "text"` 提交；
- `docs/architecture.md` 只为 ASR 保留路线图位置，并要求它与面试场景一起获得产品理由。

因此，后续研究应区分三份数据：

| 数据 | 产生时间 | 当前最接近的语义 |
| --- | --- | --- |
| 原始录音 | 浏览器录制后 | 外部输入 artifact，尚不是答案 |
| ASR transcript | Provider 返回后 | 可疑的机器转写候选，仍可被用户纠正 |
| confirmed answer | 用户确认/修改并提交后 | 现有 `answer + input_modality=voice` 契约 |

这张表是对现有契约的解释，不代表已经决定三者的持久化方式。

## 4. 浏览器录音：权限、格式与生命周期

### 4.1 麦克风权限不是普通异步函数

**标准/官方事实**

- `navigator.mediaDevices.getUserMedia({audio: ...})` 返回 `MediaStream`，并要求用户授权。
- 它只能用于 secure context；MDN 明确把 HTTPS、`file:///` 和 `localhost` 列为安全上下文示例。因此当前
  loopback-only Web 的开发形态可以申请麦克风，但未来远程部署不能使用普通 HTTP。
- 拒绝授权与找不到设备分别可形成 `NotAllowedError`、`NotFoundError`；设备已授权但系统或硬件不可读可形成
  `NotReadableError`。
- 用户可以一直忽略权限弹窗，Promise 因而既不 resolve 也不 reject。
- 浏览器必须显示麦克风正在使用或已经获得权限的指示。

来源：[MDN `getUserMedia()`](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)、
[W3C Media Capture and Streams](https://www.w3.org/TR/mediacapture-streams/)。

**对 TheGrandQuiz 的含义（待设计验证）**

- `requesting_permission` 与 `recording` 不能被当作同一状态；权限等待可能无限长。
- 权限拒绝、无设备、设备占用/不可读应成为不同的安全错误类别，不能都显示“录音失败”。
- 结束或取消录音时必须对每个 `MediaStreamTrack` 调用 `stop()`；只隐藏录音按钮不会释放设备。

### 4.2 MIME 是运行时结果，不是产品常量

**标准/官方事实**

- `MediaRecorder` 可由浏览器自行选择容器与 codec，也可以接受应用给出的偏好。
- `MediaRecorder.isTypeSupported(mime)` 只能说明当前 user agent 是否声称支持该组合。
- 实际格式可从 `MediaRecorder.mimeType` 和 `dataavailable` 事件里的 `Blob.type` 获得。
- `MediaRecorder.state` 自身只有 `inactive / recording / paused`；它不是上传或转写状态机。

来源：[W3C MediaStream Recording](https://www.w3.org/TR/mediastream-recording/)、
[MDN `MediaRecorder`](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder)。

**对 TheGrandQuiz 的含义（待 prototype 验证）**

- 不能把“浏览器录音 = WAV”写进契约。候选格式应逐个探测，最终上传时携带真实 MIME。
- 扩展名不能单独充当可信格式；服务端仍需校验 MIME、文件头、大小和所选 Provider 的能力。
- 浏览器支持和 Provider 支持是两个集合；两者交集、是否需要转码应由实测决定。

### 4.3 chunk 不等于可独立重传的音频文件

**标准/官方事实**

- `MediaRecorder.start(timeslice)`、`requestData()` 或 `stop()` 会通过 `dataavailable` 产生 Blob。
- `timeslice` 只是近似时间，浏览器调度、锁屏等因素会造成延迟和异常大的 chunk；MDN 建议使用独立计时器，
  不要靠 chunk 数量计算时长。
- W3C 明确指出：多个 Blob 被返回时，**单个 Blob 不必可播放**，只要求一段完成录音的全部 Blob 组合后可播放。
- 过大的 `timeslice` 或长期缓存可造成卡顿和内存耗尽。

来源：[W3C MediaStream Recording §2.3/§6.1](https://www.w3.org/TR/mediastream-recording/)、
[MDN `dataavailable`](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event)。

**对 TheGrandQuiz 的含义（待设计验证）**

- “每秒产一个 Blob”不能直接推导出“每秒一个可独立 ASR/重传文件”。
- 真正的断点续传需要额外定义 chunk 序号、完整性、组合方式、最终提交和临时数据清理；它不是
  `MediaRecorder` 免费赠送的能力。
- 对短答案，先验证完整 Blob 上传是否已经足够；不要因为 `timeslice` 存在就提前实现分片协议。

## 5. ASR 的四种任务形态

### 5.1 完整文件、同步结果

典型接口接收一个完整音频文件，在同一 HTTP 请求中返回转写。OpenAI 官方 Python SDK 的
`audio.transcriptions.create` 接收 file + model，支持 FLAC/MP3/MP4/M4A/OGG/WAV/WebM 等输入，并可返回
JSON、文本或字幕形态（具体能力依模型而异）。

来源：[OpenAI Python SDK transcription resource（由官方 OpenAPI 生成）](https://github.com/openai/openai-python/blob/main/src/openai/resources/audio/transcriptions.py)。

它的特点是：

- 应用侧任务简单；
- 完整音频已经存在后才发起请求；
- 请求超时/连接断开时，客户端未必知道服务端是否已经完成；
- 没有独立 operation/task 的接口，就没有可轮询的 Provider 状态。

### 5.2 完整文件、流式返回文字

OpenAI 同一 transcription 接口的部分模型允许 `stream=true`，以 SSE 返回 transcript events。输入参数仍是一个
完整 `file`，所以它改善的是**结果展示延迟**，不是把麦克风音频实时送入模型。官方 SDK 还明确指出
`whisper-1` 不支持这一 stream 选项。

来源：[OpenAI Python SDK transcription resource](https://github.com/openai/openai-python/blob/main/src/openai/resources/audio/transcriptions.py)。

对本项目最重要的概念区分是：

```text
complete file upload + output SSE deltas != live microphone input streaming
```

### 5.3 实时输入流、实时结果流

Google Cloud Speech-to-Text V2 的 `StreamingRecognize` 是 gRPC 双向流：第一条消息负责配置（如果 Recognizer
尚未完全配置），后续消息发送 inline audio；服务端返回一系列 response。结果可以是：

- `is_final=false` 的 interim hypothesis，后续可能变化；
- `is_final=true` 的已稳定片段；
- 可选 voice activity events 与起止静音超时；
- confidence 只在特定结果上出现，并且官方明确说不保证总是提供或准确。

来源：[Google StreamingRecognize guide](https://cloud.google.com/speech-to-text/docs/streaming-recognize)、
[Google Speech-to-Text V2 RPC reference](https://cloud.google.com/speech-to-text/v2/docs/reference/rpc/google.cloud.speech.v2)。

阿里云百炼 Paraformer 的实时协议提供了另一种一手样例：

1. 建立 WSS；
2. 客户端发送带 UUID `task_id` 的 `run-task`；
3. 收到 `task-started` 后才能发送单声道二进制音频；
4. 服务端发送 `result-generated`；
5. 客户端发送 `finish-task`；
6. 收到 `task-finished` 后关闭或按协议复用连接。

其 Python SDK 同时提供阻塞式本地文件 `call()` 与 `start → send_audio_frame → stop` 的 callback 流式接口，
官方建议流式 packet 约 100 ms、1–16 KB。不同模型接受的实时格式也不同。

来源：[阿里云 Paraformer WebSocket API](https://help.aliyun.com/en/model-studio/websocket-for-paraformer-real-time-service)、
[客户端事件](https://help.aliyun.com/en/model-studio/paraformer-client-events)、
[服务端事件](https://help.aliyun.com/en/model-studio/paraformer-server-events)、
[Python SDK](https://help.aliyun.com/en/model-studio/paraformer-real-time-speech-recognition-python-sdk)。

**值得记录的官方文档漂移**：本次访问时，Google streaming guide 写每条消息 25 KB，而 V2 RPC reference 对
`audio` 字段写 15 KB。它说明实现前必须冻结“所选 API 版本 + 官方契约 + 真机测试”，不能把跨版本示例值写成
项目永恒常量。

### 5.4 异步长任务

Google batch recognition 返回 `Operation`，调用方轮询 operation 获取最终结果；Google 的 Long-Running
Operations 取消语义是 best effort，取消成功也要继续查询 operation 才能确认最终状态。

阿里云非实时文件转写使用 submit-then-poll：POST 创建后获得 Provider `task_id`，再 GET task 查询；部分接口支持
回调。Paraformer 文档说明只能取消仍处于 `PENDING` 的任务。某些异步接口要求 Provider 可以访问的音频 URL，
这会额外引入对象存储和访问授权边界。

来源：[Google batch recognition](https://cloud.google.com/speech-to-text/docs/batch-recognize)、
[Google Long-Running Operations](https://cloud.google.com/apis/design/design_patterns#long_running_operations)、
[阿里云非实时语音识别](https://help.aliyun.com/en/model-studio/non-realtime-speech-recognition-user-guide)、
[阿里云 Paraformer HTTP API](https://help.aliyun.com/en/model-studio/paraformer-recorded-speech-recognition-restful-api)。

**对 TheGrandQuiz 的含义（不作最终选型）**

- 短面试答案可能不需要 batch job，但必须通过实测时长与延迟证明。
- 本地 `voice_run_id`、HTTP `request_id`、Provider `task_id/request_id/operation` 不是天然同一个身份。
- 若选择需要公网音频 URL 的异步接口，就会改变 local-first 的隐私和部署边界；不能把它当成普通 Adapter 差异。
- streaming transcript 还必须定义 delta、snapshot、interim 和 final 的归并规则。

## 6. 取消、重试、幂等和“断点续传”

### 6.1 三层取消不能混为一个按钮语义

| 层 | 官方能保证的动作 | 不能自动推断的事情 |
| --- | --- | --- |
| 浏览器录音 | `MediaRecorder.stop()` 结束采集并触发最终 Blob；track `stop()` 释放设备 | 后端上传/Provider 已取消 |
| 浏览器请求 | `AbortController.abort()` 可终止 fetch、response body consumption 或 stream | 已到达服务端的计算一定停止 |
| Provider task | 依 Provider 合约关闭流或调用 cancel；Google LRO 为 best effort | cancel 请求成功返回即代表任务必定没完成 |

来源：[MDN `AbortController.abort()`](https://developer.mozilla.org/en-US/docs/Web/API/AbortController/abort)、
[Google Long-Running Operations cancel semantics](https://cloud.google.com/apis/design/design_patterns#long_running_operations)。

因此，“用户点击取消”需要在后续设计中回答：它是停止监听结果，还是还要终止本地 task、关闭 Provider stream、
阻止 transcript 被提交，以及最终如何确认 terminal state。

### 6.2 重试与幂等

RFC 9110 把幂等定义为多次相同请求与一次请求具有相同的预期服务端效果，并明确要求客户端不要自动重试非幂等
请求，除非知道其实际语义幂等，或能证明原请求没有生效。ASR 创建/转写通常经 POST 发起，不能因为输入音频相同
就假设 Provider 会去重。

来源：[RFC 9110 §9.2.2 Idempotent Methods](https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods)。

后续设计必须分开回答：

- 用户是否在重试同一个本地任务，还是明确创建一次新尝试；
- 原 Provider 请求是否已经成功但响应丢失；
- Provider 是否公开支持 idempotency key；
- 同一音频重试后返回不同 transcript 时，以哪个结果进入 review；
- 哪些错误可重试（网络、限流、临时服务故障），哪些不可重试（权限拒绝、格式不支持、超限）。

### 6.3 “断点续传”也有两种不同问题

1. **音频上传恢复**：已经录完，传输中断后继续传未上传的 bytes。
2. **实时识别恢复**：WebSocket/gRPC 中断后，继续同一语义 utterance 并保持上下文、interim/final 一致性。

`MediaRecorder` chunk 不能直接解决第一种；Provider 没有 resume protocol 时，重连也不能自动解决第二种。阿里云
实时文档要求连接复用时每个 task 使用不同 `task_id`，失败会关闭连接，并建议实现客户端重连，但没有因此承诺
旧 task 可精确续传。

## 7. 音频、transcript 与隐私

### 7.1 Provider 策略不能由统一 Adapter 名字掩盖

**OpenAI 官方事实**

- API 数据默认不用于训练，除非客户显式 opt in；
- API 仍可能产生 abuse monitoring logs 和 application state；
- 默认监控、ZDR/MAM、区域处理能力需按账号和 endpoint 核对。

来源：[OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data)。

**Google Cloud STT 官方事实**

- 未加入 data logging opt-in 时，内容只用于提供 STT 服务；
- sync/streaming 音频在内存中处理，不保存 customer data，但会临时记录部分请求 metadata；
- async endpoint 会保存转写结果约 5 天以供取回，输入音频不由 STT 服务保存；
- 可用特定 endpoint 限制在 EU 或 US 多区域处理，但不支持单一区域限制。

来源：[Google Cloud STT data usage FAQ](https://cloud.google.com/speech-to-text/docs/data-usage-faq)。

**对 TheGrandQuiz 的含义（待形成产品政策）**

- “本地 Web”不等于“音频不出本机”；界面必须说明实际 Provider 与数据去向。
- 原始音频、Provider 临时结果、用户确认文本和 Trace metadata 应有分别的保留/删除政策。
- Provider 的 retention、region 和训练 opt-in 是配置事实，应进入后续选型验收，而不是隐藏在统一 Provider 名下。
- 若未来保存“机器 transcript → 用户修订 transcript”用于 Eval，需要显式授权与来源标记，不能自动晋升。

## 8. 成熟开源项目的一手实现模式

这些案例用于培养实现审美，不表示应照搬其产品决策。

### 8.1 Open WebUI：显式取消/确认，Provider 差异留在后端

[`VoiceRecording.svelte`](https://github.com/open-webui/open-webui/blob/01f4282f1ffe0d6212f58d3afbeae21fffd0c4be/src/lib/components/chat/MessageInput/VoiceRecording.svelte)
展示了这些做法：

- `getUserMedia` 请求 echo cancellation、noise suppression 和 auto gain control；
- 依次探测 WebM/Opus、WebM、OGG/Opus、MP4、WAV，记录 `MediaRecorder` 实际 MIME；
- 取消时解除 recognition `onend`，防止取消动作误触发确认；
- 停止所有 track、清空 chunks、释放 wake lock；
- 只有用户确认后才组合 Blob 并调用后端 transcription；
- 后端结果回到父组件。

父组件
[`MessageInput.svelte`](https://github.com/open-webui/open-webui/blob/01f4282f1ffe0d6212f58d3afbeae21fffd0c4be/src/lib/components/chat/MessageInput.svelte)
默认把 transcript 插入输入框；自动发送是单独的 `speechAutoSend` 设置。后端
[`audio.py`](https://github.com/open-webui/open-webui/blob/01f4282f1ffe0d6212f58d3afbeae21fffd0c4be/backend/open_webui/routers/audio.py)
把多个 STT engine 隔离在统一入口，并负责输入校验、格式处理和 Provider 分派。

**可借鉴的品味**：资源清理路径是录音功能的一等代码；确认录音、获得 transcript 和发送消息不是天然同一个动作。

### 8.2 LibreChat：区分已有草稿与转写文本，但采用“完成即发送”

[`AudioRecorder.tsx`](https://github.com/danny-avila/LibreChat/blob/main/client/src/components/Chat/Input/AudioRecorder.tsx)
把浏览器 STT 和 external STT 分开处理，并在录音开始前保留已有输入框文字，避免转写覆盖草稿。官方
[`#9318`](https://github.com/danny-avila/LibreChat/pull/9318) 又针对 external STT 完整 snapshot 与浏览器累计文本
的不同语义修复重复/覆盖问题。

LibreChat 在完成后会进入发送链，这适合 hands-free chat，但不当然适合正式考核。

**可借鉴的品味**：Provider 事件必须声明它是 delta 还是完整 snapshot；已有草稿、已提交请求和迟到 transcript
之间要有明确竞态保护。

### 8.3 AnythingLLM：转写进入草稿，自动提交是显式偏好

[`SpeechToText/index.jsx`](https://github.com/Mintplex-Labs/anything-llm/blob/master/frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/SpeechToText/index.jsx)
使用浏览器 Speech Recognition，将累计识别结果与前一版本比较后只追加增量；静音后结束录音；
`autoSubmitSttInput` 是独立配置，关闭时 transcript 留在可编辑输入框。

**可借鉴的品味**：`transcript → editable draft → submit` 是可靠默认值，自动发送是需要用户显式选择的策略。

## 9. 对用户当前六点理解的逐项校准

### 9.1 “最终拿到 JSON，所以语音就是带 metadata 的 user_message”

前半句不总成立：输出可能是 JSON、text、SSE events 或流式 interim/final。后半句要增加一个确认门：只有用户
确认后的文本才是正式 `user_message/answer`；录音和机器 transcript 是它的来源证据，而不是同一事实。

### 9.2 状态链

`recording → uploading → transcribing → reviewable → submitted` 是合理的产品视图候选，但资料显示至少有三位
owner：浏览器 capture、网络 transfer、Provider recognition。权限等待、失败、取消和重试不能只当作旁边几个
布尔值；后续设计要明确每个 owner 的状态与聚合规则。

### 9.3 现有接口与持久化

现有 `answer + input_modality=voice` 已经给“确认后的语音答案”留出位置。尚缺的是 pre-submit transcription
run，而不是第二套语音判卷契约。

### 9.4 AgentEvent 信封

只在一个 start/end 之间塞最终大对象会丢失权限失败、上传失败、Provider retry、取消是否生效等事实。Provider
原生接口本身就有 started/result/finished/failed 或 operation status；后续事件设计至少要能解释这些边界，
同时避免把音频 bytes 和完整 Provider payload 写入 Trace。

### 9.5 时长、大小、类型与幂等

不存在脱离产品和 Provider 的“业界统一最佳数字”。应先用桌面 Chromium、实际 Provider、中文/英文专业词和
目标回答长度跑 prototype，再冻结首发限制。标准已经给出的硬规则是：运行时 MIME 探测、独立计时、资源上限、
POST 不盲重试、取消后仍确认终态。

### 9.6 Provider 对接

Provider 的主要变化轴不只是字段名，而是：

- whole-file / live stream / async job；
- multipart / base64 / Provider 可访问 URL / binary frames；
- snapshot / delta / interim / final；
- 是否有 task ID、查询和取消；
- 接受的容器、codec、采样率、语言提示和专业词热词；
- retention、region 与数据 opt-in。

一个统一接口只有在这些差异被真实 prototype 观察后，才知道应该隐藏什么、保留什么。

## 10. 下一轮学习与 prototype 应回答的问题

这些是研究产生的问题清单，不是已经批准的实现计划。

1. 在目标桌面 Chromium 上，MediaRecorder 实际产出哪些 MIME/codec？
2. 目标 Provider 能否直接接受该 Blob，还是必须转码？
3. 30 秒、90 秒、3 分钟中文回答的音频大小、上传时间、首个 transcript 时间与完成时间分别是多少？
4. “RAG、ReAct、PageIndex、AgentEvent、FastAPI”等词在无提示、language hint、hotword/context hint 下表现怎样？
5. 关闭浏览器 fetch 后，后端和 Provider 分别处于什么状态？
6. 网络在上传前、上传中、已上传但未收到响应时断开，怎样区分安全重试与可能重复执行？
7. 用户取消后是否还会收到迟到 callback/SSE/WebSocket event？它会不会污染草稿或提交答案？
8. transcript 经用户修改后，哪些 metadata 对调试有价值，哪些属于不应持久化的音频隐私？
9. Provider 的 confidence/word timestamp 是否稳定到足以成为产品契约，还是只应留在可选审计字段？
10. 上传式识别的体验是否已经满足“答一道再判一道”，还是实时 interim 确实产生用户可感知收益？

只有这些问题有了真实答案，才适合进入 PRD、状态机与 Provider seam 的正式设计。

## 11. 主要一手来源

- [W3C Media Capture and Streams](https://www.w3.org/TR/mediacapture-streams/)
- [W3C MediaStream Recording](https://www.w3.org/TR/mediastream-recording/)
- [MDN `getUserMedia()`](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
- [MDN `MediaRecorder`](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder)
- [MDN `dataavailable`](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event)
- [MDN `AbortController.abort()`](https://developer.mozilla.org/en-US/docs/Web/API/AbortController/abort)
- [RFC 9110 HTTP Semantics §9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods)
- [OpenAI official Python SDK: transcriptions](https://github.com/openai/openai-python/blob/main/src/openai/resources/audio/transcriptions.py)
- [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data)
- [Google Cloud StreamingRecognize guide](https://cloud.google.com/speech-to-text/docs/streaming-recognize)
- [Google Cloud Speech-to-Text V2 RPC](https://cloud.google.com/speech-to-text/v2/docs/reference/rpc/google.cloud.speech.v2)
- [Google Cloud batch recognition](https://cloud.google.com/speech-to-text/docs/batch-recognize)
- [Google Cloud STT data usage FAQ](https://cloud.google.com/speech-to-text/docs/data-usage-faq)
- [阿里云 Paraformer WebSocket API](https://help.aliyun.com/en/model-studio/websocket-for-paraformer-real-time-service)
- [阿里云 Paraformer Python SDK](https://help.aliyun.com/en/model-studio/paraformer-real-time-speech-recognition-python-sdk)
- [阿里云非实时语音识别](https://help.aliyun.com/en/model-studio/non-realtime-speech-recognition-user-guide)
- [Open WebUI `VoiceRecording.svelte`](https://github.com/open-webui/open-webui/blob/01f4282f1ffe0d6212f58d3afbeae21fffd0c4be/src/lib/components/chat/MessageInput/VoiceRecording.svelte)
- [LibreChat `AudioRecorder.tsx`](https://github.com/danny-avila/LibreChat/blob/main/client/src/components/Chat/Input/AudioRecorder.tsx)
- [AnythingLLM `SpeechToText`](https://github.com/Mintplex-Labs/anything-llm/blob/master/frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/SpeechToText/index.jsx)

## 附录 A：Qwen-Audio-3.0-ASR-Flash-Streaming 官方契约核对

> 核对日期：2026-08-11
>
> 资料边界：阿里云百炼官方模型表、用户指南、WebSocket 事件参考和官方 SDK 示例
> 目的：补充模型事实并辨认迁移边界；不据此决定 v0.5 Provider 或最终架构

### A.1 模型身份已确认，但它不是旧模型的别名

官方模型表和实时语音识别指南都列出了精确模型 ID：
`qwen-audio-3.0-asr-flash-streaming`。它在华北 2（北京）和新加坡地域均有文档入口，属于
**Qwen-Audio-3.0-ASR-Flash-Streaming** 实时识别产品线。

旧链路使用的 `qwen3-asr-flash-realtime-2026-02-10` 则属于
**Qwen3-ASR-Flash-Realtime** 的日期快照。官方把两者同时列在支持模型清单中，并为它们链接到不同的
API 参考，因此不能把新模型理解为旧模型的稳定别名，也不能只替换 `model` 字符串后沿用旧事件解析。

来源：[百炼语音识别模型表](https://help.aliyun.com/zh/model-studio/asr-model/)、
[实时语音识别用户指南](https://help.aliyun.com/en/model-studio/real-time-speech-recognition-user-guide)。

说明：在当前 TheGrandQuiz 工作树、ignored 文件和 Git 历史中均未检索到旧模型字符串。因此下面的“旧链路”
比较基于已知硬编码模型 ID 和官方协议，不代表已经审计到那份封存代码的具体 SDK 调用与事件处理。

### A.2 新模型的输入与传输形态

| 问题 | 官方可确认事实 |
| --- | --- |
| 是完整文件 HTTP，还是实时音频流？ | 主接口是 WebSocket 双工实时识别：客户端先发 `run-task`，收到 `task-started` 后持续发送二进制音频。官方也示范读取本地文件后按块发送，但这仍是 WebSocket 流，不是一次性 HTTP 文件上传。 |
| 还有别的协议吗？ | 还支持 AOQ。官方建议客户端在重视弱网、稳定延迟、全双工降噪和回声消除时评估 AOQ；普通 WebSocket 仍是明确支持的入口。 |
| 音频格式 | 只接受单声道输入；格式可为 `pcm`、`wav`、`mp3`、`opus`、`speex`、`aac`、`amr`。`opus/speex` 要用 Ogg 封装，`wav` 要用 PCM 编码，`amr` 只支持 AMR-NB。 |
| 采样率 | API 参考称 8 kHz 专用模型只接受 8000 Hz，其他模型接受任意采样率；新模型不是 8 kHz 专用模型。官方麦克风与文件样例均使用 16 kHz。实现前仍应以目标浏览器音频和一次真实调用验证，而不是只依赖样例注释。 |
| 单次时长/大小 | 模型表标为“无限制”。这不是浏览器、WebSocket 或产品可以无限录音的保证：未启用 heartbeat 时，持续静音 60 秒会超时；客户端仍需设置自己的时长、内存和上传上限。 |
| 本地文件是否可用 | 可以。官方样例把文件读取为字节块，每约 100 ms 发送一次，并在结束后发 `finish-task`。它适合做协议验证，但不是断点续传协议。 |

来源：[客户端事件与参数](https://help.aliyun.com/en/model-studio/fun-asr-client-events)、
[实时语音识别用户指南](https://help.aliyun.com/en/model-studio/real-time-speech-recognition-user-guide)、
[百炼语音识别模型表](https://help.aliyun.com/zh/model-studio/asr-model/)。

### A.3 interim、final 与任务终态

新模型复用 Qwen-Audio-3.0-ASR-Flash-Streaming/Fun-ASR-Realtime 的任务协议：

```text
connect
  -> run-task(task_id, model, audio config)
  <- task-started(task_id)
  -> binary audio frames ...
  <- result-generated(sentence_end=false)  # interim，可被后续结果修订
  <- result-generated(sentence_end=true)   # 当前句 final
  -> finish-task(task_id)
  <- task-finished(task_id)                 # 整个任务正常终态
```

失败时收到 `task-failed`，其中有 `error_code` 与 `error_message`，连接随后关闭且不能复用。
`result-generated` 既承载 interim 也承载句级 final；消费者必须依据
`payload.output.sentence.sentence_end` 判断，不能把每个回调都 append 成新文本。句级结果还提供
`sentence_id`、句级起止时间和词级 `words[].begin_time/end_time/text/punctuation`。只有
`sentence_end=true` 时，时间戳与该句结果才是最终值，且 `usage.duration` 才出现。

来源：[服务端事件](https://help.aliyun.com/en/model-studio/fun-asr-server-events)、
[客户端事件](https://help.aliyun.com/en/model-studio/fun-asr-client-events)。

### A.4 `task_id`、取消、重试与幂等边界

**`task_id`**

- 由客户端生成，格式为 UUID；`run-task`、`continue-task`、`finish-task` 和服务端事件都用它关联任务。
- 官方 SDK 结果中还可读取 `request_id`。它是 Provider 请求标识，不应与产品自己的 `voice_run_id` 或
  WebSocket `task_id` 合并成同一个字段。
- 复用连接启动下一任务时，必须换新的 `task_id`。

**取消**

- `finish-task` 的官方语义是“所有音频已经发送完，请正常结束并产出结果”，不是“撤销且丢弃结果”。
- 官方 Python SDK 的 `recognition.stop()` 对应正常停止流程；原始 WebSocket 也可直接关闭，但文档没有定义
  `cancel-task` 事件，也没有承诺关闭连接后远端计算必然撤销或不计费。
- 因而 UI 的“取消”至少要在本地阻止迟到 transcript 晋升为草稿或答案；是否还能获得 Provider
  `task-finished`，必须通过 prototype 验证，不能从 `stop()` 名字推断。

**重试与幂等**

- 官方生产建议是在 `on_error` 后清理当前连接、等待后建立全新连接，并给出了最多三轮一类的客户端重连示例。
- `task-failed` 后旧连接不能复用；正常 `task-finished` 后才可复用连接，并且新任务必须使用新 `task_id`。
- 已审阅的官方事件与 SDK 文档没有说明相同 `task_id`、相同音频或 `run-task` 具备幂等去重语义，也没有实时
  音频断点续传协议。因此网络中断后的“重试”应视为新的 Provider 尝试；应用自己决定旧尝试如何终结、是否重发
  完整音频，以及怎样防止两个结果竞争。

来源：[实时语音识别用户指南的生产建议](https://help.aliyun.com/en/model-studio/real-time-speech-recognition-user-guide)、
[客户端事件](https://help.aliyun.com/en/model-studio/fun-asr-client-events)、
[服务端事件](https://help.aliyun.com/en/model-studio/fun-asr-server-events)。

### A.5 与旧 `qwen3-asr-flash-realtime-2026-02-10` 的协议差异

| 维度 | 新 `qwen-audio-3.0-asr-flash-streaming` | 旧 `qwen3-asr-flash-realtime-2026-02-10` |
| --- | --- | --- |
| 产品线 | Qwen-Audio-3.0-ASR-Flash-Streaming | Qwen3-ASR-Flash-Realtime 日期快照 |
| WebSocket 端点/协议 | DashScope inference 任务协议；`run-task` + 二进制帧 + `finish-task` | Realtime session 协议；`session.update` + Base64 `input_audio_buffer.append` + `session.finish` |
| 主身份 | 客户端 UUID `task_id` | 服务端 `session.id`、客户端 `event_id`、每个 utterance 的 `item_id` |
| 中间结果 | `result-generated`，`sentence_end=false` | `conversation.item.input_audio_transcription.text`，`text` 是稳定前缀、`stash` 是可变草稿 |
| 最终结果 | 同一事件 `result-generated`，`sentence_end=true`；任务另有 `task-finished` | `conversation.item.input_audio_transcription.completed`；会话另有 `session.finished` |
| 音频格式 | 七类格式，文档说明上述封装限制 | 官方推荐 `pcm` 或 `opus`；其他格式可能通过参数校验却在服务端解码失败 |
| 分段参数 | `semantic_punctuation_enabled`、`max_sentence_silence`，默认 VAD | `session.turn_detection`、`silence_duration_ms`；还支持显式 manual commit |
| 时间戳 | 句级与词级时间戳默认提供 | 当前不返回时间戳 |
| 精度增强 | 即时/预编译热词、Prompt/对话上下文；运行中可 `continue-task` 更新上下文 | 官方模型表列为不支持精度增强 |
| 情感识别 | 官方模型表列为不支持 | 始终返回七类细粒度 emotion |
| 连接复用 | 正常 `task-finished` 后可用新 `task_id` 开下一任务 | session 结束后应关闭，不支持连接复用 |

来源：[新模型客户端事件](https://help.aliyun.com/en/model-studio/fun-asr-client-events)、
[新模型服务端事件](https://help.aliyun.com/en/model-studio/fun-asr-server-events)、
[旧模型交互流程](https://help.aliyun.com/en/model-studio/qwen-asr-realtime-interaction-process)、
[旧模型服务端事件](https://help.aliyun.com/en/model-studio/qwen-asr-realtime-server-events)、
[模型能力对比](https://help.aliyun.com/zh/model-studio/asr-model/)。

**迁移风险结论**：二者属于同一“实时语音转文字”能力类别，但不是同一事件协议。若旧封存代码直接解析
`session.*`、`conversation.item.*`、`text + stash`，或者依赖 emotion，它不能通过更改模型名迁移。新模型的
`result-generated` 是句级 snapshot，解析器、终态、错误映射和连接生命周期都要单独适配。反过来，新模型的词级
时间戳、热词与上下文是值得 prototype 的能力，但暂时不能被写成 TheGrandQuiz 的公共必选字段。

### A.6 官方 SDK 能做什么

官方 Python/Java 示例已把以下细节封装在 `Recognition` 类中：

- 建立和鉴权 WebSocket；
- `start/call` 后通过 `send_audio_frame` 持续发送字节；
- callback 接收中间结果、句结束、完成和错误；
- `stop()` 发起正常结束；
- 暴露 `request_id`、首包延迟和末包延迟等指标。

这意味着可以直接用 SDK 快速做 Provider prototype，不必先手写 WebSocket。但 SDK callback 仍暴露 Provider
协议语义；它没有替项目决定草稿归并、取消后的迟到结果隔离、本地幂等、Trace 脱敏与业务终态。官方也说明 SDK
主要封装连接管理、鉴权和重连，原始 WebSocket 在需要自定义连接控制时仍可使用。

来源：[实时语音识别用户指南及 SDK 示例](https://help.aliyun.com/en/model-studio/real-time-speech-recognition-user-guide)、
[DashScope 官方 Python SDK 仓库](https://github.com/dashscope/dashscope-sdk-python)。

### A.7 价格与免费额度：当前只能确认到哪里

官方通用规则可确认：

- ASR 需要在对应 workspace 单独开通模型访问；
- 免费额度按模型独立，快照和稳定模型也可能分别计额；
- 剩余额度和过期时间以控制台的“免费额度”或模型广场为准，显示为分钟级更新；
- 新人额度有有效期，过期后未用完部分也失效；已认证账号在额度耗尽后可能继续产生按量费用，可按控制台能力开启
  “免费额度用完即停”。

但截至本次核对，公开静态价格表的 ASR 章节尚未出现
`qwen-audio-3.0-asr-flash-streaming` 的独立价格/免费额度行；它仍只列出旧 Qwen3-ASR-Realtime 和
Fun-ASR 系列。因此，**无法从当前公开静态文档稳定确认这个新模型的具体免费秒数、地域、有效期与超额单价**。
若用户控制台显示免费额度，应把“账号、region、workspace、模型 ID、剩余秒数、过期时间、截取日期”一起记录为
一次运营事实，而不能把它写成架构常量或 README 承诺。

来源：[新人免费额度 FAQ](https://help.aliyun.com/zh/model-studio/new-free-quota/)、
[百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)。

### A.8 仍需 prototype 才能回答的不确定项

1. 当前账号控制台所示免费额度是否真的绑定精确模型 ID、哪个 region/workspace、何时到期，以及耗尽后的行为。
2. DashScope Python SDK 的当前安装版本能否直接调用新模型；官方文档只要求安装最新版，没有为此模型冻结最低版本。
3. 桌面 Chromium 的实际 WebM/Opus Blob 是否能被新模型直接稳定消费，还是首发需要 PCM 采集/服务端转码。
4. `recognition.stop()`、直接断开和网络失败分别会产生哪些迟到 callback、最终 usage 与计费结果。
5. 同一段中文面试回答使用新旧模型时，专业词准确率、首个 interim、句级 final、整体完成延迟与费用差异。
6. 新模型的句级 snapshot 在重复修订时怎样稳定归并；尤其不能复用旧模型的 `text + stash` 逻辑。
7. AOQ 对桌面 Web + 本地 FastAPI 的真实收益和部署代价；本轮只确认其存在，未研究其 SDK、鉴权和浏览器边界。

## 附录 B：Codex 语音体验不能简单等同于 Whisper

用户在 Codex 中感受到的“语音功能”至少要分成两种产品形态：

- **Voice**：实时双向会话，支持自然轮替和打断。OpenAI 当前官方文档称其由 GPT-Live 驱动。
- **voice dictation**：把语音先转成输入框中的 prompt 文本，用户确认后再发送。Codex app 26.429 的更新记录明确提到
  `dictation cleanup`，以及可配置的听写词典（姓名、文件路径和代码符号）。

官方资料没有披露 Codex 输入框听写使用的底层 ASR 模型，因此不能声称它由 Whisper 实现。用户观察到的“去掉嗯、
那个那个”等效果，也不能全部归因于基础 ASR：它可能来自听写清理、词典或其他未公开处理。

对考核产品而言，通用听写的“更顺滑”与答案忠实度之间存在额外张力。自动改写可能意外补充知识、改变否定关系，甚至
替用户改善答案。因此后续若实验清理能力，应保持三个边界：

1. 原始 ASR transcript 只作为临时证据；
2. 清理只生成可编辑建议，不自动提交；
3. 首版限制在标点、大小写、明显重复口头填充词和材料内专业词词典，不做语义补全。

若以后使用 LLM 清理，应展示 diff，并记录清理器版本、模型与用户是否再次编辑；否则无法区分 ASR 质量、清理质量和
用户最终表达，也无法建立可信 Eval。

来源：[ChatGPT Voice](https://learn.chatgpt.com/docs/features/voice)、
[ChatGPT & Codex changelog](https://learn.chatgpt.com/docs/changelog)。

## 附录 C：Prototype 01 的工程可行性结论

> 运行日期：2026-08-11
>
> 证据等级：单机真实 Provider 工程可行性验证；不是 ASR 精度 benchmark

桌面 Chromium 的 `MediaRecorder` 实际产出 `audio/webm;codecs=opus`。该 Blob 经 loopback FastAPI 读取后，
以 Base64 Data URL 直接提交给 `qwen-audio-3.0-asr-flash`，不需要转码。一次 37.275 秒、531,628 bytes 的
录音得到 2.026 秒 Provider 往返延迟；响应包含稳定 `request_id`、`sentence_end=true`、词级时间戳和
`usage.duration=33`。

未增强样本把同音的 `ReAct` 识别为更常见的 `React`，并把 `AgentEvent` 拆成 `Agent event`。owner 又使用
另一段真实录音确认：开启即时术语词表后，表内术语识别明显改善，表外术语仍不稳定。这与产品拥有 exact 当前材料
scope 的条件吻合，支持把“当前材料小型 glossary”作为首版精度增强手段。

因此停止扩大本轮语料，并作以下有限结论：

- 录完上传 → 可编辑 transcript → 用户确认提交的首版交互具有工程可行性；
- `qwen-audio-3.0-asr-flash` 可作为首个真实 Provider Adapter；
- ASR glossary 应由获批 `ResourceRevision` 的高信号术语生成，是可重建投影，不是 `KnowledgeItem` 的新权威字段；
- glossary 应在考核启动时按 exact resource/item scope 收窄，不把整篇原文或全库词汇无界提交给 Provider；
- 暂不引入转码、实时 WebSocket、Filetrans、横向 Provider 对比或更大测试集；
- 本结论不能表述成已通过 CER/WER、噪声、浏览器兼容性或 Provider 质量 benchmark。
