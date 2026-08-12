# ADR-0012：语音转写是可审查输入，不是正式答案

- Status: Accepted
- Date: 2026-08-11

## Context

v0.5 要把录音答题接入既有考核 workflow。浏览器录音、外部 ASR 返回的 transcript 和用户最终提交的答案
看起来都像“同一条消息”，但它们的可信度、隐私和失败语义不同：录音是外部 artifact，transcript 是模型生成的
候选文本，只有用户确认或修改后的文本才能代表学习者真正表达的答案。

如果 ASR 返回后直接进入判卷，术语词表、识别错误或迟到响应都可能改变学习状态；如果把原始音频、完整
Provider 响应和草稿都写入 LearningFactJournal，又会把短期运行数据永久提升为学习事实，扩大敏感数据面。

## Decision

语音答题分成两个串联但权威性不同的 workflow：

1. `VoiceRun` 接收完整录音，调用 `SpeechRecognitionProvider`，产出可编辑的 reviewable transcript。
2. 用户显式确认或修改后，才通过既有 Assessment answer submission 以
   `input_modality=voice` 提交；出题、判卷、申诉和学习记账继续复用原 workflow。

原始录音不进入 LearningFactJournal，也不作为 v0.5 的长期持久化对象。reviewable transcript 只允许在本地
短期保存以支持同一服务进程内的组件重挂/页面状态恢复，并在 submitted、cancelled 或 TTL 到期后清理；长期事实只保留用户最终提交的
answer 及最小 provenance。完整 Provider payload 不进入浏览器契约或长期学习事实。

取消只保证本地状态终结和迟到结果隔离，不宣称已经撤销 Provider 计算或费用。相同用户操作使用稳定
`request_id` 幂等；每次真实外部调用使用独立 `provider_attempt_id`，v0.5 不对非幂等转写请求做盲目自动重试。

材料术语增强是一次 VoiceRun 的冻结输入，而不是 Provider adapter 的启动期常量。`ASR_ENABLE_HINTS` 仅提供
未设置偏好时的首次默认；Local Settings 可热更新 `asr_material_hints_enabled`，随后创建的运行把启用状态、
hint_set_id 与有界术语快照一并冻结。已经 accepted 的运行不接受设置页的中途改写。

## Alternatives considered

- **ASR 返回后直接提交答案**：交互更短，但识别错误和词表偏置会直接污染判卷与 Learning Memory。
- **把录音和原始 transcript 永久保存在学习历史**：便于调试，却扩大隐私、迁移和清理责任，且违背
  ADR-0010 的白名单事实边界。
- **把 VoiceRun 做成第二套考核 workflow**：会复制题目身份、判卷、申诉和记账逻辑，造成 Web/CLI 漂移。
- **失败后自动重复调用 Provider**：体验看似平滑，但当前同步 HTTP Provider 没有幂等保证，可能产生重复费用
  和竞争结果。

## Consequences

- Web 必须提供 transcript 审查态，不能把“识别完成”等同于“答案已提交”。
- `VoiceRun` 是 Assessment 前的应用 workflow，不是新的学习 bounded context；最终提交仍走唯一 Assessment seam。
- 需要显式管理本地捕获状态与服务端 VoiceRun 状态，并测试取消后的迟到响应不能晋升草稿。
- 需要短期 transcript 的主动 TTL 清理和服务重启收敛策略；AssessmentSession 尚未持久化，因此重启后失去
  Assessment 绑定的草稿必须立即过期，不能谎称仍可提交。原始音频默认只存在于浏览器和当前请求内存。
- ASR Eval 若需要保留音频或原始 transcript，必须走另一个显式授权、脱敏的本地数据集流程，不能从生产运行
  静默采集。
