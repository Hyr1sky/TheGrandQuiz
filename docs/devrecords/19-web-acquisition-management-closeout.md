# Web Acquisition 管理态收口

日期：2026-07-29
范围：LW-S5，v0.1.0 发布前

## 这一步解决了什么

以前的入库像一条不能中途停下来的流水线：

```text
抓材料 → Reader 深读 → 当场审批 → 写入知识库
```

CLI 可以在终端里阻塞等待，但浏览器不能让一个 HTTP 请求一直挂着；服务一重启，内存里的候选也会丢。
本次把它深化成两个明确阶段：

```text
prepare_ingest
  抓取 → 文档树 → Reader → 精确 Evidence → PreparedIngest
                                      │
                                      ▼ 持久化 needs_input
commit_prepared_ingest
  人工选择 → 原子写入 revision / nodes / KnowledgeItem
```

`PreparedIngest` 是“已经读懂，但还没有获准进正式知识库”的快照。它包含待提交资源、候选知识点、
revision 身份和原 ingest span；因此审批前 `LearningStore` 仍为空，失败或取消也不会留下半份资料。

## 为什么重启后还能继续

`learning.db` 新增 `acquisition_runs` 台账，保存固定六态：

```text
queued → running → needs_input → succeeded
                    ├──────────→ cancelled
                    └──────────→ failed
```

当状态进入 `needs_input` 时，候选快照已经持久化。浏览器只保存一次性 resume token；服务重启后，
FastAPI 重新读取同一条 run，用户仍能看到概念、摘要、置信度和精确原文证据。token 过期或已经使用时，
状态机拒绝再次提交。

事件也没有另起炉灶。准备阶段开启的 `ingest` span 在审批恢复后继续使用同一个 `trace_id`：

```python
EventEmitter(
    sink,
    clock,
    trace_id=trace_id,
    initial_seq=next_seq,
    initial_span_counter=next_span,
)
```

这样 `approval.decided`、revision 提交和最终状态仍在同一条事件脊柱上，Trace、SSE 和测试看到的是同一
事实，而不是三套互相猜测的状态。

## Web 上具体发生什么

顶栏和空知识库都提供“添加材料”。右侧玻璃管理抽屉包含：

1. `.md` / `.markdown` / `.txt` 上传，浏览器读成 UTF-8 文本，不需要 multipart 依赖；
2. 公开 `http(s)` URL，继续使用已有 SSRF、重定向、正文质量和大小守卫；
3. 接收材料、深读、审批、写入星图四阶段状态；
4. 候选知识点逐项选择，显示摘要、置信度和短 Evidence；
5. 取消、稳定错误、重试和最近导入记录；
6. 成功后刷新资源列表并自动切换到新材料。

浏览器只接收有限的 Acquisition UI event，不会获得内部任意事件名、完整正文或 prompt。

## 验收重点

- prepare 后 Store 仍为空，commit 后只出现获批子集；
- `needs_input` 关闭并重新打开数据库后仍可恢复；
- 审批 token 单次使用；
- 服务重启后经 HTTP 完成审批，Trace sequence 不冲突；
- 运行中取消、Provider 失败、非法文件和非法 URL 均零 KB 污染；
- React 测试覆盖上传、SSE 状态、Evidence 预览、选择、审批和成功回调；
- OpenAPI、TypeScript、lint、构建和 Python 静态门保持一致。

## 发布前复审补强

双轴审查发现“知识库事务尚未提交，trace 已先写成功”会在极端提交失败时制造幽灵事实。现在持久化与
事件投递被明确分成两步：先在同一个 `learning.db` 事务里消费 token、写入快照并推进台账；事务成功后，
才追加 `approval.decided`、revision/item 与 `ingest.ended`。故回滚时 token、知识库和成功事件三者都不前进。

终态命令也改为幂等：已经 succeeded/failed/cancelled 的 run 再收到 cancel，只返回原状态，不追加冲突事件。
Acquisition 终态 payload 同时带稳定 status，因此 Observatory 能可靠显示 completed/failed/cancelled。

新增回归锁住：提交失败零成功事件、终态取消不漂移、跨重启 sequence 连续、离线 URL 成功闭环与三类
Observatory 终态。真实 URL dogfood 使用 JavaGuide RAG 基础文章，Reader 经证据重试后产出并审批 11 个
候选，原子入库 trace 为 `34b6f8c3e2084c0c90ccac27ac6d79fe`。
