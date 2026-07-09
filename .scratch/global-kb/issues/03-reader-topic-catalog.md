# GKB-S3 — Reader 抽资源级 topic（RAG-metadata）+ 目录注入上下文

Status: ready-for-agent
Type: AFK（Reader topic 真机质量属你 dogfood；建绿可用 fake provider / 我录 cassette）

## Parent
[PRD: 全局 KB 重构](../PRD.md)

## What to build

让深读 Reader 在抽 KnowledgeItem 的同时**产出一个资源级 `topic`**（"这份材料讲什么"的一句话，RAG 式 metadata
抽取），存进 `resources.topic`；并让 ReAct 上下文注入一份 **`{resource_id → topic}` 库存清单**，使 agent
**不调工具就知道库里有哪些材料**——这是 S4 目录式 scope 的前置（LLM 据清单把用户意图映射成 exact resource_id）。

## 锁定设计（不留给实现猜）

- **Reader 结构化输出加 topic**：Reader 的深读结果新增一个**资源级** `topic: str`（非空、一句话主题名）。
  更新 reader prompt 让模型额外产 topic；pydantic schema 校验（沿用缝 3 有界重试：缺/空 topic → ModelRetry）。
  topic 是资源级（一份材料一个），不是 per-item。
- **落库**：`ingest_resource` 拿到 topic 后写入 `resources.topic`（S2 已建列）。资源深读失败（failed）不产 topic。
- **目录注入**：`learner_context_provider` / `render_learner_context` 加一段**库存清单**——列全库
  `{resource_id → topic}`（**按 resource_id 升序**、确定性渲染；空库→整段跳过）。抬头讲清这是"库里现有的材料，
  用户说'考X'时据此认出对应 resource_id"。为此 `Store` 加一个只读列举方法（如 `all_resources()` 或
  `resource_topics()`，两实现同序）。
- **确定性**：清单渲染是纯代码、按 resource_id 升序、无 clock/random。Reader 是 LLM 槽，走 record/replay；
  单测可用 fake/scripted provider 返回带 topic 的 JSON；真机 cassette（含 topic）由 record 脚本录制。
- **分层**：全在 domain/learning；context provider 仍只交字符串给 kernel ContextBuilder。

## Acceptance criteria

- [ ] Reader 深读输出含资源级 `topic`（非空校验、缺失→ModelRetry 有界重试）；reader prompt 更新并版本化
- [ ] `ingest_resource` 把 topic 写入 `resources.topic`；失败资源不产 topic
- [ ] `Store` 加只读资源列举方法（两实现同序、parity 测试）
- [ ] `render_learner_context` 注入 `{resource_id → topic}` 库存清单（按 resource_id 升序、空库跳过、确定性字符串）
- [ ] TDD：topic 校验门、落库、清单渲染（升序/空库/多资源）、parity，各 mutation 可杀
- [ ] Reader 真机 cassette 含 topic（record 脚本录；建期可 fake provider 顶）；既有 eval 绿
- [ ] 五门全绿（含 lint-imports）

## Files (owner, 可能漂)
`domain/learning/reader.py`、`domain/learning/prompts/`(reader prompt)、`domain/learning/ingest.py`、
`domain/learning/store.py`(资源列举)、`domain/learning/context.py`(清单渲染)、`scripts/record_ingest.py`(如需)、相关测试。

## Blocked by
GKB-S2（需 `resources.topic` 列 + 重塑后的模型）。可与 GKB-S4 / GKB-S5 并行。
