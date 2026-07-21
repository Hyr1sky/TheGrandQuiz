# Web Acquisition ReAct 收口开发记录

> 记录日期：2026-07-21
> 范围：WA-S4 真实 SearXNG + ReAct dogfood、case17、候选选择边界与失败页零污染。

## 1. 收口目标

WA-S1–S3 已经分别证明 Fetch、正文质量门、Search adapter 与 Acquisition Replay 可用，但原 case16
是代码直接编排，尚未覆盖开放 ReAct 的真实决策层。WA-S4 要证明模型不会在最终文本里伪造搜索结果，
也不会在用户选择前自动抓取；选定 URL 后，Reader、审批和入库仍必须回到确定性 workflow。

## 2. 候选选择契约

`SearchToolResult` 新增恒为 `true` 的 `selection_required`，`web_search` 工具说明明确要求返回候选后
结束当前回合并等待用户选择。case17 的规则门不依赖最终文本关键词，而是从事件流验证：搜索与成功
ingest 的 `parent_span_id` 属于不同 agent turn，成功 URL 逐字存在于搜索候选，之后才出现 Reader 与
审批事件。

SearXNG adapter 同时把显式 `domains` 转成 `site:` 查询下推，并在返回后继续执行原有域名过滤。真实
录制第一次暴露了只做后过滤会导致空召回、模型连续改写 query 的问题；修复后同一请求一次返回 5 条
`javaguide.cn` 候选。

## 3. 真实录制

录制使用 loopback endpoint `http://127.0.0.1:8080`、SearXNG `2026.7.19-6da6eee26`，公开搜索指纹为
`wa-s4:searxng-2026.7.19-json-v1`。真实模型收到三回合合成消息：先搜索 MySQL 面试高频考点；再选择
JavaGuide 的 MySQL 面试题 URL；最后尝试一个合成登录页。

最终轨迹为：

```text
turn 1  web_search(limit=5, domains=[javaguide.cn]) → 5 candidates → final
turn 2  ingest(selected JavaGuide URL) → Reader → approval → 5 items → final
turn 3  ingest(example.com/login) → login_page → 0 items → final
```

Fetch cassette 使用项目自有的合成 MySQL 正文，不保存 JavaGuide 全文；它保留 selected requested URL、
`native_http`/Trafilatura 指纹、untrusted 与 accepted 质量结论。登录页只保存稳定 `login_page` 失败。
录制使用内存 store 和脚本化 keep-all 审批，事件仍完整经过 `approval.requested/decided` 与原子 snapshot
workflow，没有写生产 learning DB。

真实 LLM cassette 包含 7 次模型调用，execution tokens 合计 36,168；两份 cassette 合计约 14 KB。
敏感词审计未发现 Authorization、Bearer、API key 或 secret。录制完成后 SearXNG 容器与临时网络均已
清理，镜像保留。

## 4. Eval 与失败保护

新增 case17 和 `grade_case17`，断言以下不变量：

- 允许开放 ReAct 在同一发现回合内做 1–3 次有界 query 调整，但所有 search 必须先于 ingest；本次
  真实基线为 1 次。
- 搜索回合与用户选择后的 ingest 回合必须不同，选中 URL 必须来自候选集。
- 成功网页以 `read + untrusted` 状态形成获批 KnowledgeItem。
- 独立低质量 URL 必须 `failed`，恰好一条结构化 fetch failure，且零 KnowledgeItem。
- Search、Tool、Reader、Approval 与 Ingest 事件全部在原事件脊柱上闭合。

Eval Harness 因此由 16 条增至 17 条；只有 case15 启用 Tier-2，其余 16 条保持确定性 Tier-1。日常
运行只读取 LLM 与 Acquisition cassette，不访问公网、不启动 Docker、不调用外部模型。

## 5. 架构边界

本轮没有把 discovery 塞进核心考核 workflow，也没有让 Search adapter 写 KB。开放 ReAct 只负责发现
候选和响应用户选择；Fetch 后的 Reader、Evidence 校验、审批、revision 与 KnowledgeItem 提交继续由
代码控制。SearXNG 仍是可拔插 adapter，未配置时 `web_search` 不注册，Docker 仍不是项目基础依赖。

## 6. 最终门禁

```text
ruff check                  pass
ruff format --check         pass（171 files）
pyright                     pass（0 errors）
lint-imports                pass（kernel layering kept）
pytest                      831 passed
python -m grandquiz.evals   17/17 passed
HTML report                 pass（case17 / 36,168 execution tokens）
```
