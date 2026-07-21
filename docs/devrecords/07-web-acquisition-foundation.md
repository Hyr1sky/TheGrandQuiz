# Web Acquisition 基座开发记录

> 记录日期：2026-07-21
> 范围：`.scratch/web-acquisition/` 的 WA-S1–S3，以及 WA-S4 的 JavaGuide Fetch 预验收。
> 当前边界：真实 SearXNG + ReAct 决策仍是 HITL；本轮没有写生产 `learning.db`、没有调用外部 LLM，也没有提交第三方文章正文。

## 1. 交付目标与架构位置

本轮把“用户已经知道 URL”之前和 Reader 之后的断点接成一条稳定基座：搜索只发现候选，用户或开放 ReAct 选择 URL，Fetch 把外部内容规范化为 `FetchedDocument`，质量门通过后才进入现有 Reader → KnowledgeItem 审批 → 全局 KB workflow。

这没有改变 ADR-0004。Search / URL 选择属于开放编排；Reader、精确 evidence、审批与入库仍是确定性 workflow。所有审计继续走 `AgentEvent` 脊柱，kernel 没有新增任何 learning domain 依赖。

## 2. WA-S1：Trafilatura 与正文质量门

生产 HTML 抽取器固定为 Trafilatura 2.1.0，输出 Markdown，并抽取 title 与 canonical URL。统一 `FetchedDocument` 同时保存 requested/final/canonical URL、content type/hash、adapter、extractor 指纹、结构化质量结论和不可信标记；LearningResource 仍按 requested URL 确定身份，不因重定向或 canonical 发生隐式改名。

质量门 fail closed 地识别空正文、过短正文、链接密集导航页、登录表单与 bot challenge。拒绝后复用现有 ingest 失败分支，已用测试证明 Reader、审批和 KnowledgeItem 都是零调用/零写入。成功 `RESOURCE_READ` 事件只记录 URL 元数据、hash、adapter/extractor 与质量结论，不记录完整正文；失败事件记录稳定 classification。

原有流式解压后大小上限、Content-Length 预拒绝、逐跳 SSRF、重定向次数、超时和 content-type 守卫保持不变。fixture corpus 用必须存在/必须不存在片段验证正文保留和导航、脚本、Cookie、页脚剔除，不绑定 Trafilatura 的内部 DOM 算法。

## 3. WA-S2：可拔插 SearchProvider 与 SearXNG

新增供应商无关的 `SearchProvider` / `SearchResult` 契约，以及直接调用 SearXNG JSON API 的 adapter。查询、返回数量、域名过滤与超时均有显式边界；无效 schema、超时、HTTP 状态和来源失败映射为稳定错误分类。

`web_search` 只返回标题、URL、摘要、adapter、rank 和有限 metadata，不自动 Fetch、Reader 或入库。只有显式注入 SearchProvider 时才注册该工具，因此未配置环境下现有 ReAct tool schema 和 cassette 指纹保持不变。CLI 通过 `SEARXNG_URL` 和可选 `SEARXNG_TIMEOUT_SECONDS` 启用；GrandQuiz 不负责启动 SearXNG，也不要求 Docker。

Search started/ended 事件记录 adapter、query hash/长度、结果上限、域名约束、结果数量或失败分类，不把原始 query 或 secret 写进 trace。商业搜索、GitHub Search 或后续受控 MCP adapter 可以实现同一接口，无需修改 Reader 和 KB workflow。

## 4. WA-S3：Acquisition Record/Replay 与 case16

新增独立 Acquisition cassette，分别录放规范化 `SearchResult[]`、`FetchedDocument` 和稳定 Fetch 失败。key 包含规范化请求、公开 adapter 指纹、cassette 版本与 extractor/normalization 版本；版本变化会显式 miss，不能继续回放旧产物假绿。cassette 不接收 Authorization header、API Key 或客户端对象。

Eval Harness 增加 case16：离线回放 search → selected URL → fetch → ingest 成功路径，再回放一个 bot challenge 失败。规则门断言成功材料保持 requested URL identity、不可信标记、adapter/extractor/quality 审计与获批入库；失败材料不增加 Reader 调用，不触发审批，不产生 KnowledgeItem。日常 Eval 因此从 15 条增为 16 条，仍只有 case15 调 Tier-2 judge。

## 5. JavaGuide 真实 dogfood

用户提供的 `https://github.com/Snailclimb/JavaGuide/tree/main/docs` 用生产 `HttpFetchSource` 只读抓取。第一次运行发现正常 GitHub HTML 的 script 资产含 `captcha` 字样，旧启发式扫描整份 raw HTML，误判为 bot challenge。回归测试先复现后，检测改为只看可见文本与登录表单结构；同一 URL 重跑成功，得到 2,627 字符、canonical/title、Trafilatura 2.1.0 指纹与 accepted 质量结论。

随后分别验证 `docs/README.md` 和具体的 `docs/database/mysql/mysql-questions-01.md` raw Markdown。MySQL 文章通过有界传输读取 40,542 字符，正文包含 MySQL、索引与事务，hash 为 `820a2309c6b489b3a7540bb2b3c31a06b50fccaf99ab567e03aa15ca797ec7ce`。raw Markdown 按 `text/plain` 保持原文，不经过 HTML 抽取；GitHub blob/tree HTML 更适合目录发现，确定性材料入库优先使用 raw URL。

真实 dogfood 只输出 URL 元数据、字符数、hash 和质量结论。没有把 JavaGuide 正文写入仓库、trace 或生产数据库，也没有发送给外部模型。

## 6. 测试、提交与当前边界

本轮按可验收切片维护 main 分支：

- `d46334b docs: split web acquisition delivery`
- `0abde38 feat(learning): gate extracted web documents`
- `92cc455 feat(learning): add optional web search adapter`
- `9387511 feat(evals): replay web acquisition boundaries`
- `58eca9b fix(learning): harden web acquisition quality signals`

最终门禁：

```text
ruff check .                pass
ruff format --check .       pass（168 files）
pyright                     pass（0 errors）
lint-imports                pass（kernel layering kept）
pytest                      819 passed
python -m grandquiz.evals   16/16 passed
```

WA-S1–S3 已完成。WA-S4 仍需要一个可达的真实 SearXNG endpoint，以及在发送 prompt/tool schema 前的明确外部 LLM 授权，用来证明真实模型会执行“搜索候选 → 选择 URL → ingest”而不是在最终文本中伪造结果。MCP Fetch/Search、浏览器型 JavaScript 抓取、整仓库批量 ingestion 与搜索排序学习均未进入本轮。
