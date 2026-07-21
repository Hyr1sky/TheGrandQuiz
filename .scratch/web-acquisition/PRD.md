# PRD：Web Acquisition（原生 Fetch / Search + 可选 MCP Adapter）

Status: delivered through WA-S3（2026-07-21：可靠 Fetch、Trafilatura、质量门、可选 SearXNG Search、
Acquisition Replay 与 case16 已完成；WA-S4 真实 SearXNG + ReAct 为 HITL，MCP / browser adapter 后置）
Triage: ready-for-human

## Problem Statement

当前 `grandquiz react` 已能接收 URL 并通过 `httpx` 抓取网页，但真实 dogfood 中这条路径基本无法稳定产出
优质学习材料：

- 传输层完整缓冲响应后才检查大小，资源上限不是实际的下载上限。
- HTML 只用标准库抽取文本，导航、页脚、推荐、Cookie 文案等 boilerplate 大量混入正文。
- JavaScript 页面、反爬页面、登录墙和 Cloudflare 等站点可能只返回壳页或挑战页。
- 没有 `web_search`，agent 只能读取用户已经知道的 URL，不能先发现候选再选择材料。
- 若直接把任意 MCP 工具挂给 ReAct，凭证、输出形状、注入防护、trace 与 replay 都会脱离现有领域纪律。

用户需要的是像 Claude Code 一样稳定的 `web_search` / `web_fetch` 工具体验，同时保留 TheGrandQuiz 的
核心优势：不可信输入防护、确定性 workflow、事件脊柱、结构化输出和可回放 eval。

## Solution

建立一个深的 **Web Acquisition module**：GrandQuiz 自己拥有稳定的搜索结果与抓取文档接口，底层由
原生 adapter 或 MCP adapter 提供能力。

用户可以先搜索得到结构化候选，再抓取选中的 URL；抓取结果经过传输守卫、正文抽取与质量门，形成统一
`FetchedDocument` 后才交给 Reader。MCP 只作为 adapter，不绕开内部模型、安全校验与 replay。

核心链路：

```text
用户意图 → web_search → SearchResult[] → 选择 URL → web_fetch
→ FetchedDocument → 正文质量门 → Reader → KnowledgeItem 候选 → 审批 → 全局 KB
```

## User Stories

1. 作为用户，我想让 agent 根据主题搜索学习材料，以便不必先手工找到 URL。
2. 作为用户，我想看到搜索结果的标题、URL、摘要和来源，以便判断哪篇值得读。
3. 作为用户，我想让 agent 抓取普通文章、技术博客和文档页，并提取真正正文而不是整页导航文本。
4. 作为用户，我想让同一个 URL 的最终跳转地址和 canonical URL 被记录，以便知道实际读了什么。
5. 作为用户，我想在网页只是登录页、挑战页或空壳时收到诚实失败，而不是把污染文本建成知识库。
6. 作为用户，我想抓取超大网页时系统尽早停止，避免 CLI 卡死或占满内存。
7. 作为用户，我想在原生抓取失败时可以选用已配置的 MCP server，而不用改学习 workflow。
8. 作为用户，我想在没有商业搜索 Key 时连接自托管搜索或 MCP 搜索能力。
9. 作为用户，我想明确知道搜索 / 抓取用了哪个 adapter，以及是否发生了 fallback。
10. 作为用户，我想在 KnowledgeItem 入库前审批 Reader 结果，防止低质量网页污染全局 KB。
11. 作为开发者，我想让原生与 MCP adapter 返回同一种内部模型，使 Reader 不认识供应商差异。
12. 作为开发者，我想把搜索和抓取分成两个工具，使“发现候选”与“读取资源”可以独立 eval。
13. 作为开发者，我想让所有网页与 MCP 内容都标为不可信输入，不能因来源是 MCP 就绕过注入防护。
14. 作为开发者，我想让网络结果可以 Record/Replay，使 eval 不依赖网页实时内容和网络状态。
15. 作为开发者，我想让 Replay 记录规范化产物与 adapter 指纹，而不是密钥或不稳定客户端对象。
16. 作为开发者，我想用真实失败页面的 fixture 比较正文抽取方案，而不是凭主观印象选择依赖。
17. 作为开发者，我想让正文质量门输出结构化原因，使 trace 能区分过短、boilerplate、挑战页和类型错误。
18. 作为开发者，我想让重定向每一跳继续经过 SSRF 检查，不能因新 adapter 回退而降低安全性。
19. 作为开发者，我想让 MCP 凭证由 MCP server 或环境配置管理，任何密钥不进入 trace、cassette 或 Git。
20. 作为开发者，我想先限制 MCP 可调用能力为 Search / Fetch 两种受控契约，避免任意远程工具动态挂载。
21. 作为开发者，我想让原生 HTTP 路径保持可单独使用，MCP 不可用时基础抓取仍可工作。
22. 作为开发者，我想把 JavaScript 浏览器抓取保留成可选 adapter，不让浏览器运行时成为基础安装负担。

## Implementation Decisions

### 1. 内部模型归一化

Web Acquisition module 定义供应商无关的结构化模型：

- `SearchResult`：标题、URL、摘要、来源 adapter、可选 rank / metadata。
- `FetchedDocument`：requested URL、final URL、canonical URL、标题、正文、内容类型、内容 hash、来源
  adapter、质量标记和不可信标记。
- `AcquisitionFailure`：稳定错误分类，如网络失败、SSRF 拒绝、超大小、类型不支持、正文过短、挑战页、
  adapter 未配置。

模型不保存墙上时间；时序由事件流的注入 Clock 表达。正文 hash 以规范化后正文计算，原始响应是否另存
artifact 由实现 issue 决定，但不得默认把超大 body 塞进 trace。

### 2. 原生 Fetch Adapter

- 使用异步流式 HTTP；按**解压后的累计字节数**执行硬上限，超限立即关闭响应。
- 保留逐跳重定向与每跳 SSRF 校验；限制 scheme、跳数、超时和内容类型。
- HTTP transport 只负责可靠、安全地取得响应，不自行决定哪些 DOM 文本是正文。
- 原生 adapter 无 API Key，是默认基础能力。
- 稳定性加固 SH-S3 先交付本节及 `FetchedDocument` 最小地基，不同时引入 search / MCP。

### 3. 正文抽取与质量门

- 正文抽取是传输后的独立实现职责，输入已受大小限制的 HTML，输出标题 / canonical URL / 正文。
- 先建立真实 fixture corpus，对标准库基线、`trafilatura`、`readability-lxml` 等候选做外部行为对比，
  再锁定生产依赖。
- 质量门至少识别：空 / 过短正文、链接或导航密度异常、重复文本异常、登录 / Cookie / bot challenge
  页面、标题正文明显不相干。
- 质量门失败不得调用 Reader，不得创建 KnowledgeItem；失败原因经领域事件上脊柱。
- 不要求 v1 绕过付费墙、登录墙或专业反爬。

### 4. Search Interface 与 Adapter

- `web_search(query)` 只返回结构化候选，不自动 ingest 搜索结果全文。
- “原生工具”指 GrandQuiz 拥有稳定工具接口，不意味着搜索供应商天然无 Key。
- 首个直接搜索 adapter 建议支持 SearXNG（可自托管、避免把商业 Key 作为基础前提）。
- 商业搜索 adapter 后续按真实需求添加，凭证只从环境变量读取。
- 查询、返回数量、域名约束和超时均为显式参数；返回数量有保守上限。

### 5. MCP Adapter

- MCP 是 Search / Fetch 接口的 adapter，不直接替代领域 workflow。
- 第一版建议支持本地 `stdio` MCP server；远程 Streamable HTTP 留真实部署需求出现后再加。
- 不在第一版把任意 MCP 工具动态注册进 ToolRegistry；只调用配置中明确映射的 Search / Fetch 工具名。
- MCP 输出必须映射成内部模型，再经过大小、质量、不可信与注入守卫。
- MCP server 自己管理上游 API Key；GrandQuiz 配置只引用 server command / env 名，不记录 secret value。
- server 不可用、输出 schema 错误或超限时走稳定错误分类，不允许静默切到错误材料。

### 6. Workflow 与事件脊柱

- Search / Fetch 属开放 ReAct 编排：模型可以决定是否搜索、选哪个候选、是否抓取。
- Reader、审批与入库仍是确定性 workflow；LLM 不直接写 KB。
- 事件至少覆盖 search/fetch started/ended、adapter、查询或 URL、结果数量、内容 hash、质量结论与错误分类。
- 事件 payload 默认不存完整网页正文；正文进入受控 artifact / resource store，trace 只记摘要和 hash。

### 7. Record / Replay

- 外部搜索与抓取结果要有独立 Record/Replay adapter，回放 normalized `SearchResult[]` / `FetchedDocument`。
- key 包含请求参数、adapter 类型、公开配置指纹和规范化版本；不得包含 Key、Authorization header。
- 正文抽取规则或规范化版本变化应使旧 artifact 大声失效或显式迁移，不能继续假绿。
- ReAct eval 需覆盖“搜索 → 选择候选 → fetch → ingest”真实决策轨迹，而不只直调 fetch 函数。

### 8. Fallback 策略

- v1 不做无条件多 adapter 自动回退；自动回退会把失败语义、成本和供应商选择藏进实现。
- agent 可以看到结构化失败后显式选择另一个 adapter，或由后续确定性策略按配置的优先序裁决。
- 浏览器型抓取可作为未来 adapter，用于 JavaScript 页面；不得成为基础安装依赖。

## Testing Decisions

- **原生传输**：使用 `httpx.MockTransport` / 可控字节流，断言超限时未消费剩余 body、连接关闭、
  重定向逐跳校验、压缩后超限、超时与内容类型错误。
- **正文抽取**：fixture corpus 保存输入 HTML 与必须存在 / 必须不存在的正文片段；不锁定内部 DOM 算法。
- **质量门**：测试外部结论与结构化原因，覆盖文章、文档、导航页、登录页、bot challenge、空页。
- **adapter parity**：Native 与 MCP 对等 fixture 映射成同一内部模型，字段与错误分类一致。
- **MCP**：使用本地脚本化 server / transport fake，验证 stdio 生命周期、schema 错误、超限和凭证不落 trace。
- **Record/Replay**：录一次 search/fetch normalized artifact，再断网回放并比较事件序、hash 与最终入库结果。
- **领域不变量**：质量失败零 Reader 调用、零 KnowledgeItem；审批拒绝零入库；所有输入仍是不可信。
- **ReAct 轨迹**：至少一条真录 cassette 证明模型确实调用 search/fetch/ingest，而非在最终文本伪造结果。
- 测试断言可观察行为，不断言使用了哪个第三方抽取函数；候选库选择由 corpus 报告支持。

## Out of Scope

- 绕过付费墙、登录认证、验证码、Cloudflare challenge 或网站访问控制。
- 默认安装 Playwright / 浏览器二进制；浏览器 adapter 另立 issue。
- 任意 MCP 工具发现后直接动态挂载给 ReAct。
- 把搜索结果自动全部抓取、Reader、入库；候选选择和审批仍保留。
- 搜索排序学习、个性化推荐、向量检索、跨资源概念归并。
- 让 MCP 内容跳过 SSRF、大小、质量或注入守卫。

## Confirmed Decisions

以下推荐默认值已由用户确认：

1. **正文抽取**：用户于 2026-07-21 进一步确认直接采用 `trafilatura`；fixture corpus 对比现有标准库基线，不再额外引入 `readability-lxml`。
2. **首个直接搜索 adapter**：SearXNG 已实现为可选 endpoint adapter；商业 Key adapter 后置。
3. **MCP transport**：本轮未实现；后续出现真实需求时仍先做本地 `stdio`，Streamable HTTP 后置。
4. **MCP 暴露范围**：只做受控 Search / Fetch 映射，不动态挂载任意工具。
5. **JavaScript 页面**：v1 诚实失败，浏览器 adapter 后置。

## Further Notes

- MCP 解决的是能力与凭证所有权解耦，不会凭空消除上游 API Key；Key 可以由用户选择的 MCP server 管理。
- 原生 Fetch 与 MCP Fetch 是同一真实 seam 上的两个 adapter；Reader 只认识 `FetchedDocument`。
- package 位置在 issue 设计时再据共同改动历史与依赖方向决定，不为视觉整齐预建空目录。
- 本 PRD 不改变 ADR-0004：开放发现可 ReAct，值得 eval 的 Reader → 审批 → 入库仍是 workflow。
