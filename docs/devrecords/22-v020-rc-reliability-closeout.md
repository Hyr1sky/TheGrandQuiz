# v0.2 RC：Evidence 与错误反馈闭环收口

日期：2026-07-30

这一轮没有增加新的学习能力，只修复两个会直接破坏试玩体验的可靠性问题。

## Markdown 看起来一样，source 却不一样

真实失败 trace 中，Reader 收到的 Markdown source 是 `do\_inter\_process\_publish`，模型按渲染后的可见
文本返回 `do_inter_process_publish`。旧代码只做逐字搜索，因此一个 Evidence 失败会让整个大 batch
重新调用模型，三次后仍失败。

现在代码只在非代码 Markdown 节点中处理 CommonMark 允许的 ASCII punctuation backslash escape，并要求
可见 quote 在声明节点中唯一出现。定位成功后仍保存原始 source slice、raw offsets 和 raw quote hash：

```text
model quote:  do_inter_process_publish
visible match: unique
stored quote: do\_inter\_process\_publish
```

这不是模糊匹配；零匹配或多匹配仍然拒绝提交。代码节点中的反斜杠保持字面语义，不参与映射。

## 错误为什么以前“看得见失败，却看不见原因”

`prepare_ingest()` 已经知道 `quote_mismatch`，但返回的 `IngestResult` 没有失败契约。Web adapter 只能把它
压成 `acquisition_failed`；同时领域失败没有发 `EventType.ERROR`，所以 Observatory 状态显示 failed，
`error_count` 却是 0。

现在 `IngestFailure` 只携带三个安全字段：

```text
code   = quote_mismatch
stage  = evidence_validation
reason = Evidence 引文无法精确定位到原文节点
```

同一信封进入 Acquisition ledger、失败 AgentEvent、Trace error 统计、CLI 和 Web 管理态。内部 exception、
具体失败 quote、正文和 prompt 不进入浏览器投影。schema v15 使用一对一
`acquisition_run_failures` 扩展表保存 stage，使迁移可以安全重放。

## 明确没有做什么

- 没有加入 Reader batch 并发；真实样本只有一个 batch，并发无收益。
- 没有实现 candidate-level LLM repair；当前确定性表示修复已经消除该样本的三次重试。
- 没有建立 Trace explain Agent，也没有暴露 raw payload。
- 没有把新字段加入 Learning Model。

## 验收

- Python：`940 passed`；
- Eval：`17/17`；
- Web unit：`43 passed`；
- Playwright：桌面/移动端 `14 passed`；
- Ruff check/format、Pyright strict、import-linter、Web lint/typecheck 与生产构建全绿。

原始材料仍需进行一次真实 dogfood，确认 trace
`7691605f19eb40c585dc039c2063e088` 对应场景从 `fixed` 转为 `verified`。
