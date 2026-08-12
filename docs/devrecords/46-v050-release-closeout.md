# v0.5.0 发布收口：冻结能力，而不是继续加功能

> 日期：2026-08-12
> 状态：本地 release gate 已通过；等待 push、GitHub Actions 与 owner 的 tag/Release 授权

## 这次“收口”具体做了什么

收口不是再实现一轮功能，而是确认三件事：代码真的做到了什么、安装包实际会带上什么、我们对用户承诺什么。

本轮以 `v0.4.0...HEAD` 为完整审查面，把发布内容固定为三组独立提交：

1. Voice Interview：完整录音转写为可编辑草稿，再走唯一 Assessment 提交；
2. Runtime status：Chat `/status` 展示上下文预算和可审计 Token；
3. Desktop workspace：连续文章、可调两侧栏、统一设置与交互层级。

实时 ASR、TTS、双工数字人、移动端承诺和数据飞轮继续留在版本边界外。

## 一处典型的“文档比代码更厚”

设计稿原先写了浏览器 `recorded` 状态和上传前回放确认，但稳定实现的按钮语义实际是“结束录音并识别”：

```text
idle -> requesting_permission -> recording -> uploading -> reviewing
```

这并不等于机器草稿自动提交。录音上传后仍可回放，转写仍需用户修改/确认，正式答案边界没有变化。发布前没有
临时扩充新的 `recorded` 状态，而是把设计契约收窄到真实行为，并在 Release Notes 明说“结束后立即上传”。

同样，VoiceRun 建立后的持久错误拥有 `code/stage/reason/retryable`；建立前的 HTTP 边界错误复用全局
`code/message/retryable/trace_id`。这两层都是安全、稳定契约，但职责不同，文档现在不再把它们混写。

## 发布门发现的真实漂移

Web 源码测试全部通过时，Python 包内的 `static/` 仍是上一次构建结果。`npm run build:package` 和随后
`git diff` 正确暴露了这件事：如果直接打 wheel，朋友会安装到旧界面。重新同步带内容哈希的静态资源后，安装
产物才和源码一致。这也是 production package build 必须进入 CI 的原因。

双轴审查还发现 Chat composer 与隐藏 radio 丢失键盘焦点。修复没有把 outline 塞回 textarea，而是让外层
composer 在 `:focus-within` 时显示 token 化焦点环，并把隐藏 radio 的焦点投影给可见 label。

## 本地证据

- Ruff / format：通过；
- Pyright：0 errors；import contract：1 kept / 0 broken；
- Python：1076 passed；离线 Eval：17/17；
- Web：lint、typecheck、69 unit、OpenAPI、Sites worker、production build 通过；
- Playwright：23 passed / 1 skipped（跳过项是明确非目标的移动端语音）；
- wheel/sdist：0.5.0；仓库外安装的 CLI、离线 report 与 loopback Web smoke 通过；
- Standards / Spec：无 P0/P1；两个 P2 分别以代码修复和契约收窄关闭。

## 保留到发布后的债务

- Chat `/status` 与 Observatory 各有一份 Token usage 解析，可抽成共享只读 projector；
- production Web 主 chunk 约 546 kB，先记录体积警告，等有真实首屏指标再决定拆包；
- 四条 ASR 音频只够打开这个材料词表功能，不够声明通用准确率。

这些问题都不会造成数据破坏、安全绕过或核心流程不可用，因此不阻止 v0.5.0；但它们已经进入发布清单，避免
“没修就等于不存在”。
