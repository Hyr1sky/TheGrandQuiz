# R1-S4 — 真机 ReAct CLI（把对话 agent 接进真机通道）

Status: blocked（R1 收尾；待 S2b/S3 凑齐完整对话体验后暴露）
Type: HITL（真机对话体验 + 品味，需人试）

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## Why
S2 起工具在 test harness 里组装证明，`register_learning_tools` 尚未接进真机 CLI——还没有 `grandquiz react`/chat
命令能调这些工具。等 S2b（交互考核）+ S3（ContextBuilder）凑齐完整对话循环后，加一个真机对话入口暴露之。

## Acceptance criteria（草稿）
- [ ] `grandquiz react`（或等效 chat 命令）：真 OpenAICompatProvider + 组装 ToolRegistry（ingest/query_weak/next_question/submit_answer）+ ContextBuilder，落 trace 到独立库
- [ ] 端到端真机：一句"入库这篇然后考我薄弱点"跑通 ingest→(问答多回合)→薄弱小结
- [ ] 事件流 Rich 呈现复用现有 printer（脊柱投影）
- [ ] 顺手加固 _ScopedEmitter（__getattr__ 委托或组装，去掉 partial-subclass 脆弱）

## Blocked by
S2b（交互考核）、S3（ContextBuilder）
