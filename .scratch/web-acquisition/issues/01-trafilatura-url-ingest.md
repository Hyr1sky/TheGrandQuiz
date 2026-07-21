# WA-S1 — Trafilatura 可靠 URL 入库

Status: ready-for-agent
Type: AFK

## Parent

[PRD：Web Acquisition（原生 Fetch / Search + 可选 MCP Adapter）](../PRD.md)

## What to build

交付一条可独立验收的网页入库竖切：受边界保护的 HTTP 响应经 Trafilatura 规范化为
`FetchedDocument`，通过结构化正文质量门后，才进入现有 Reader → 审批 → 全局 KB workflow。
`requested_url` 继续作为资源身份；跳转地址、canonical URL、标题、adapter / extractor 指纹和质量结论
作为可审计元数据，不改变既有 LearningResource 身份规则。

本 issue 执行用户在 2026-07-21 确认的选择：生产正文抽取器直接采用 Trafilatura；仍以 fixture corpus
证明相对现有标准库基线的外部行为收益，不额外引入 readability-lxml。

覆盖 PRD User Stories：3–6、10、13、16–18、21。

## Acceptance criteria

- [ ] 普通文章 / 文档 HTML 被规范化为包含标题、canonical URL 和 Markdown 正文的 `FetchedDocument`
- [ ] fixture corpus 用“必须存在 / 必须不存在”断言证明导航、脚本、Cookie 等 boilerplate 不进入正文
- [ ] 空页、正文过短、导航页、登录页与 bot challenge 返回稳定、结构化的质量失败原因
- [ ] 质量失败复用现有 ingest 失败路径：Reader 零调用、KnowledgeItem 零创建、审批零触发
- [ ] transport 继续执行流式解压后大小上限、逐跳 SSRF、跳转数、超时和 content-type 守卫
- [ ] 正文 hash 基于规范化正文；trace 不保存完整网页 body，不记录墙上时间
- [ ] requested URL 保持资源 identity，final / canonical URL 不造成同一资源的隐式改名
- [ ] 生产依赖与锁文件固定 Trafilatura，静态四门和相关 pytest 全绿

## Blocked by

None - can start immediately.
