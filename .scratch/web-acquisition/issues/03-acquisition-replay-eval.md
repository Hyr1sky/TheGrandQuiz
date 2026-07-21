# WA-S3 — Acquisition Record/Replay 与 Eval

Status: done（2026-07-21；Acquisition cassette、失败回放与离线 case16 完成）
Type: AFK

## Parent

[PRD：Web Acquisition（原生 Fetch / Search + 可选 MCP Adapter）](../PRD.md)

## What to build

为 Search / Fetch 外部边界增加规范化 Record/Replay，并建立一条离线 eval tracer bullet，保护
“搜索候选 → 选择 URL → 可靠抓取 → Reader / 审批 / 入库”主路径以及“质量失败零 KB 污染”不变量。
默认 eval 必须断网可跑；cassette 保存内部模型与公开指纹，不保存 Authorization、API Key 或完整 trace body。

覆盖 PRD User Stories：14–15，并落实 Testing Decisions 中的 replay、adapter parity 与领域不变量。

## Acceptance criteria

- [x] Search replay 保存 / 回放规范化 `SearchResult[]`；Fetch replay 保存 / 回放规范化 `FetchedDocument`
- [x] replay key 包含规范化请求、adapter 类型、公开配置与 extractor / normalization 版本
- [x] key、cassette、trace 均不包含 secret、Authorization header 或不稳定客户端对象
- [x] extractor / normalization 版本变化使旧 cassette 显式 miss / 失效，不产生静默假绿
- [x] 新增离线 case16 覆盖 search → selected URL → fetch → ingest 的可观察行为
- [x] case16 同时断言正文质量失败时 Reader 零调用、审批零触发、KnowledgeItem 零创建
- [x] 默认 `python -m grandquiz.evals` 与 pytest 不访问公网，不依赖 SearXNG 或外部 LLM
- [x] 事件序、adapter、内容 hash、质量结论与最终入库结果在 Replay 中保持确定性

## Blocked by

WA-S1 and WA-S2.
