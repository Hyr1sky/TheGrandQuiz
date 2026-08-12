# v0.5 Voice Interview：把“机器听到的”安全地接进“用户真正回答的”

> 日期：2026-08-11
> 状态：代码 RC；双轴审查、真实 Provider dogfood 与术语 paired-audio 质量门均已通过

## 全局蓝图

这轮没有新做一套“语音版考核”，而是在原有考核提交之前加了一段可恢复的输入工作流：

```text
材料 revision
  -> RecognitionLexicon（可重建术语投影）
  -> TranscriptionHints（只取当前题目的小词表）
  -> 浏览器 MediaRecorder（原始音频只在浏览器和当前请求内存）
  -> SpeechRecognitionProvider
  -> VoiceRun.reviewable_transcript（短期机器草稿）
  -> 用户确认或修改
  -> AssessmentManager.submit_answer(input_modality="voice")
  -> 原有判卷 / 申诉 / Attempt / Learning Memory
```

最重要的职责边界是：`VoiceRun` 只负责“录音怎样变成可审查草稿”，`Assessment` 仍然负责“什么算正式答案、
怎样判卷和怎样记账”。这样 CLI、文字回答和语音回答不会各自长出一套规则。

## 五层实现分别做了什么

### 1. RecognitionLexicon：从已批准材料派生术语

`recognition_lexicon.py` 从 KnowledgeItem 概念、文档标题、代码标识符和 approved tag 确定性地产生 revision 级
不可变投影。它不是新的学习事实，删掉后可以从原材料重建。

选择器随后只为当前 `item_id` 生成最多 50 个 `TranscriptionHints`，避免把整库热词塞给 Provider，造成跨材料
污染。入库批准和词表重建共享同一个 SQLite 事务，因此不会出现“知识点已经切换、词表还停在旧 revision”的
半完成状态。

### 2. SpeechRecognitionProvider：隔离厂商协议

产品层只认识这条窄接口：

```python
async def transcribe(request: TranscriptionRequest) -> TranscriptionResult: ...
```

DashScope Adapter 负责 Data URL、区域 endpoint、百炼响应结构和 403/429/timeout 等错误映射；VoiceRun 不读取
厂商原始 JSON。Record/Replay cassette 只保存音频 SHA-256、请求形状和脱敏结果，不保存音频 bytes。

术语增强在开发阶段刻意默认关闭；2026-08-12 的固定音频 paired gate 已证明本轮小词表改善目标术语，且未向
负样本插入词表词。`ASR_ENABLE_HINTS` 作为首次默认；Web 设置页可持久、热更新材料词表策略，之后创建的
VoiceRun 才会向百炼发送 vocabulary，便于材料级灰度与回退。

### 3. VoiceRun：两个状态机，不把 UI 状态混进服务端

浏览器负责权限、录音和 Blob；服务端负责持久业务状态：

```text
accepted -> transcribing -> reviewable -> submitted
                    |             |
                  failed       expired
                    |
              explicit retry

任何非终态 -> cancelled
```

每次外部调用有独立 `provider_attempt_id`，最多两次且只能由用户显式重试。同一 `request_id` 与相同音频返回原
VoiceRun；换音频复用 key 会得到 conflict。取消先写本地终态，再 best-effort 取消 task，因此 Provider 的迟到
结果只能关闭 attempt，不能把已取消任务重新晋升为 reviewable。

原始音频不落盘。reviewable transcript 存在独立 `voice.db`，同一服务进程中的组件重挂可以凭
`sessionStorage` 找回；30 分钟后由后台 sweep 主动清除。AssessmentSession 当前仍是内存态，因此完整服务重启
后没有绑定对象的草稿会立即过期，不能再提交。服务重启发现 `accepted/transcribing` 会统一收敛为
`failed(code=service_restarted)`，不会永远显示“运行中”。

取消还有一个不明显的竞态：用户可能在音频 POST 尚未返回 `voice_run_id` 时点击取消。前端会中止上传并用
request ID 写入服务端取消 tombstone；即使 start 稍后才到达，也会 fail closed，不会留下后台继续转写的孤儿任务。

### 4. Web：录完先听、先改，再提交

`VoiceAnswerControl` 使用浏览器原生 MediaRecorder，不引入新依赖。当前只在后端启用 ASR 且题目为开放题时显示：

- 请求麦克风权限；
- 显示 90 秒倒计时并确保离开录音态后释放所有 track；
- 可回放本地录音；
- 展示上传、识别、取消、重试和安全错误状态；
- 空白回答可直接接收 transcript；如果录音前已有文字，则明确让用户选择“替换”或“追加”，不会静默覆盖；
- 最终通过 VoiceRun submit 间接调用唯一 Assessment submit。

实现中发现了一个典型 React StrictMode 故障：开发模式会模拟一次 effect 卸载/重挂，第一次 cleanup 把
`mountedRef` 置为 false 后没有重置，导致后端已经 reviewable，UI 仍停在“识别中”。修复方式是在每次 effect
挂载时显式恢复 mounted 状态，并用 operation generation 隔离取消后的迟到异步结果。

### 5. Trace 与验收

VoiceRun 和 ProviderAttempt 继续使用唯一 AgentEvent 脊柱。取消、服务停止与崩溃恢复都会补齐 attempt ended；
显式重试在同一 trace 内开启新的 run span，不会挂到已经失败的父 span。Trace 只记录 ID、状态、MIME、bytes、耗时、attempt、
hint_set_id/count、是否实际启用 hints 和安全错误；测试逐字检查音频、transcript、术语正文不会出现。

本轮新增或扩展的验收包括：

- 词表确定性重建、approved tag、exact-item 有界选择；
- DashScope 成功、403、429、timeout、畸形响应与无音频 cassette；
- VoiceRun 幂等冲突、取消迟到结果、显式重试、重启收敛、TTL 和唯一 voice Attempt；
- Web 权限拒绝、录音资源释放、识别取消、可编辑草稿；
- 桌面 Chromium Scenario Bot 的真实 FastAPI + 临时 SQLite 全竖切。

## 当前仍然没有声称完成的事情

1. 没有实时 WebSocket、interim transcript、TTS、双工面试官、数字人或移动端兼容承诺。
2. 没有把音频或 ASR 草稿进入数据飞轮；正式学习事实仍只有用户确认后的答案。
3. 四条固定音频只能回答本项目小词表的净收益，不能表述成通用 CER/WER 或噪声鲁棒性 benchmark。

## 真实 paired-audio dogfood（2026-08-12）

owner 显式授权后，四条固定 WebM/Opus 音频分别以同一模型运行 hints off/on，共 8 次
`qwen-audio-3.0-asr-flash` 调用，8/8 成功并完成 8/8 离线 cassette replay：

- 术语正样本：`React → ReAct`、`Agent Event → AgentEvent`，同时修正 `Retrace → 写入 Trace` 和重复“成成对”；
- 中英文混合样本：`Page Index → PageIndex`；表外 `RecognitionLexicon` 仍未正确识别，符合小词表边界；
- 负样本：on/off transcript 逐字一致，未插入 `RAG / ReAct / PageIndex / AgentEvent / FastAPI`；
- 自然回答：on/off transcript 逐字一致，没有为不相关片段强行加入术语；
- 平均 Provider 延迟：off 1,985.25 ms，on 2,038.25 ms，词表模式在本轮增加约 53 ms（2.67%）。

因此本轮预注册的“目标术语改善 + 负样本零插入”质量门通过。报告与 cassette 位于 gitignored 的
`localtemp/v050-asr-dogfood-20260812/`；只保存音频 SHA-256、大小、脱敏 transcript、稳定 request ID 和延迟，
不保存音频 bytes、API Key 或 Provider raw response。代码能力仍以显式材料词表开关控制，避免把这四条
样本夸大成所有材料都应默认增强。

## 2026-08-12：把 Prototype 开关变成正式设置

Prototype 里的 `hints off/on` 现在对应 Web 顶栏齿轮里的“启用材料词表”。设置页把三类状态刻意分开：

- 界面主题留在浏览器；
- 出题语言、难度倾向和材料词表开关写入本机 Preference Memory，CLI/Web 共享；
- LLM 与 ASR Key 只显示“是否已配置”、模型和 endpoint host，原文仍只在 `.env`。

难度没有被简化成一个会覆盖全库的滑杆。每个知识点继续保留自己由答题证据演化出的 1–5 档；设置页的
`偏基础 / 自适应 / 偏挑战` 只在下一次出题时对当前档做 -1 / 0 / +1 的有界偏移，不回写 DifficultyLedger。

词表开关也从 Provider 构造期常量改成 `TranscriptionRequest.material_hints_enabled`：设置热更新后，新的
VoiceRun 冻结本次策略；已 accepted 的运行继续使用自己的 `hints_applied` 与术语快照。这样页面展示、
Trace 和真正发给 DashScope 的 vocabulary 不会漂移。

确定性回归结果：Python `1076 passed`，Web unit `67 passed`，Playwright 桌面/移动视口
`23 passed / 1 skipped`；Ruff、Pyright、import-linter、Web lint/typecheck/build、OpenAPI 生成和 Sites
worker 均通过。移动端跳过项仍是本版明确不承诺的麦克风录音，不是设置页回归。现在可以进入 owner Web
真机录音验收；这一步会验证真实浏览器权限和真实 DashScope，而不会重复验证已经通过的状态机逻辑。
