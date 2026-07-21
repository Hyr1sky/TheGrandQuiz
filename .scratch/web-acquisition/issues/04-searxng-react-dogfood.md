# WA-S4 — 真实 SearXNG + ReAct dogfood

Status: done（2026-07-21；真实 SearXNG + ReAct/Reader 录制、case17 离线回放与失败页零污染均通过）
Type: HITL

## Parent

[PRD：Web Acquisition（原生 Fetch / Search + 可选 MCP Adapter）](../PRD.md)

## What to build

在 WA-S1–S3 全绿后，用用户提供 / 启动的真实 SearXNG endpoint 与真实模型验收开放编排：以
“深入学习 MySQL，尤其是面试高频考点”为例搜索候选，让用户筛选 URL，再抓取、Reader、审批并入库。
真实 ReAct 轨迹经明确授权后录成 cassette；Docker 只是一种可选部署方式，不进入基础开发 / 测试要求。

JavaGuide `docs` 可作为无搜索依赖的真实 fetch dogfood 材料，但 GitHub 目录页若被质量门识别为导航页，
应诚实失败并改用其中具体文章 URL，不为通过验收而放宽质量门。

覆盖 PRD Testing Decisions 中的真实 ReAct 轨迹，并验证 User Stories：1–5、8–10。

## Acceptance criteria

- [x] 记录实际 SearXNG endpoint / 版本和非敏感配置指纹，任何 secret 不进 Git / trace / cassette
- [x] 真实模型先调用 `web_search` 得到有界候选，不在最终文本中伪造搜索结果
- [x] 用户选择候选后才执行 fetch / ingest，Reader 与审批仍由确定性 workflow 控制
- [x] 至少一个优质文章成功形成可信的 `FetchedDocument` 和可审批 KnowledgeItem 候选
- [x] 至少一个目录 / 挑战 / 低质量页面诚实失败且零 KB 污染
- [x] 真录 cassette 离线回放通过，成本和事件轨迹进入 Eval / HTML 报告
- [x] devrecord 记录真实限制、人工操作、验收结果与后续 adapter 选择，不把 SearXNG / Docker 变成强依赖

## Blocked by

WA-S1, WA-S2 and WA-S3. Requires a reachable SearXNG endpoint and specific authorization before sending
prompts / tool context to the configured external LLM.

## Comments

- 2026-07-21：用户提供的 JavaGuide `docs` 目录页与具体 MySQL 面试 Markdown 已通过生产 Fetch 路径只读 dogfood；没有写生产 DB，也没有向外部 LLM 发送材料。
- 2026-07-21：WA-S5 已用最小单容器 SearXNG `2026.7.19-6da6eee26` 完成真实直接搜索，返回 JavaGuide 等 5 条候选并通过 trace 审计；验收后容器与临时网络已清理。
- 2026-07-21：case17 用同版 loopback endpoint 完成真实 ReAct 录制。模型第一回合调用一次 `web_search` 返回 5 条候选并停住；第二回合只对用户选中的 JavaGuide URL 进入 ingest / Reader / approval，形成 5 个获批 KnowledgeItem；第三回合把合成 login page 分类为 `login_page`，零 KB 污染。
- 两份 cassette 均已做敏感词审计；日常 Eval 只离线 Replay。录制使用内存 store 与合成 Fetch 正文，没有写生产 DB，也没有把 JavaGuide 全文提交进仓库。配置入口仍是 `SEARXNG_URL`；正常 `react` 运行不负责启动 Docker。
