# LW-S7 — Local Web 构建、同源启动与 v0.1.0 发布门

Status: blocked
Type: AFK

## Parent

[PRD：Local-first Web 学习工作台](../PRD.md)

## What to build

把生产 React build 作为明确 package/release artifact，由 FastAPI 同源托管；提供本地启动命令、健康检查、
配置和隐私文档，并把前端 lint/type/test/build、OpenAPI drift 和 installed-wheel smoke 纳入 CI 与
`docs/open-source-release-checklist.md`。

## Acceptance criteria

- [ ] 一条文档化命令在 127.0.0.1 启动可用 Web
- [ ] production build 同源访问 `/api/v1`，无默认宽松 CORS
- [ ] wheel/sdist 或明确的 release bundle 包含所需静态资源
- [ ] CI 检查 Python 五门、Eval、frontend lint/type/test/build、OpenAPI drift 和安装产物
- [ ] README/SECURITY/CONTRIBUTING 说明外部 LLM、DB、trace、搜索 provider 和 Web 限制
- [ ] 干净环境完成资源浏览与 fake/replay 问答 smoke
- [ ] 发布前真实 dogfood trace 由用户验收

## Blocked by

LW-S3–LW-S6 and the open-source release checklist.
