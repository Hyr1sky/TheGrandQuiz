# EJ-S4 — 真实校准、Replay 与收口

Status: done（2026-07-19；真实 deepseek-v4-flash 校准/录制、离线报告、五门与文档收口完成）
Type: HITL

## Parent

[PRD：Tier-2 LLM Grader 与质量评测闭环](../PRD.md)

## What to build

使用已授权的测试内置合成材料和外部 LLM 显式录制 calibration + case15 judge cassette。只有人工区间 gate 全通过才采用录制结果；随后在断网 Replay 下完成双 Tier 报告、成本复算、五门、开发记录和 conventional git 收口。

覆盖 PRD User Stories：2–4、11–18、22–25、30–32。

## Acceptance criteria

- [x] 显式录制脚本只发送合成 calibration samples、case15 问题/回答/参考证据和版本化 judge prompt
- [x] 真实 judge 对全部 calibration 阻断性维度落入人工区间；分歧必须先审计，不手改模型输出
- [x] cassette 保存 resolved model、role、prompt/request 指纹与 usage；不含密钥或生产材料
- [x] 默认 run_all/report 在 Replay 下零网络、稳定通过；删除/篡改 cassette 必须大声失败
- [x] case15 Tier-1/Tier-2 均绿，execution/judge tokens 与 prompt versions 可复算
- [x] HTML index/detail 真跑可打开，rule/quality/cost/rationale/evidence 完整可见
- [x] Ruff check、Ruff format check、Pyright、import-linter、全量 pytest 与 Eval report 全绿
- [x] PRD/EJ-S1–S4、开发日志和 README/CLI discoverability 回填完成，git 历史规范并推送 main
- [x] DS-S5 继续关闭；judge 不写 production learning.db、不自动修改 prompt 或难度

## Blocked by

- [EJ-S3](03-dual-tier-report-cli.md)
