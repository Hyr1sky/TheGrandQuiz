# SH-S3 — 异步流式 Web Fetch 硬限制

Status: ready-for-agent
Type: AFK

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

把当前完整缓冲后检查大小的抓取改为异步流式传输，在解压后累计内容超过限制时立即终止，同时保留 SSRF、
逐跳重定向、超时和内容类型守卫，并产出 Web Acquisition 后续可复用的最小结构化抓取结果。

## Acceptance criteria

- [ ] 抓取不阻塞 async workflow，所有响应与客户端在成功和失败路径关闭
- [ ] 解压后累计字节一旦超限立即停止消费后续 body
- [ ] SSRF 检查覆盖初始 URL 与每个重定向目标
- [ ] scheme、跳数、超时、HTTP 状态与内容类型失败走稳定错误分类
- [ ] 结构化结果至少含 requested/final URL、正文、content type 与 content hash
- [ ] MockTransport / 可控流测试证明超限时未完整读取响应
- [ ] 现有本地文件 ingest 与 fetch 失败领域语义不回归
- [ ] 五门全绿

## Blocked by

- [SH-S0](01-authoritative-doc-baseline.md)
