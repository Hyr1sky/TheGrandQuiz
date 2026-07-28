# RC-S2 — 安装包运行资产自包含

Status: done
Type: AFK

## Acceptance criteria

- [x] PyYAML 是 `grandquiz report` 的运行依赖，不再只存在于 dev group
- [x] case14–17 与 Tier-2 cassette 位于 `grandquiz.evals` 包内
- [x] 运行时不依赖仓库根目录或 `tests/fixtures`
- [x] 包内资产名称与路径穿越有确定性契约测试
- [x] 从仓库外安装 wheel 后 `grandquiz --help` 和离线 report 17/17

## Evidence

2026-07-28 干净 Python 3.12 临时环境安装 wheel；报告生成 17/17，wheel 列表包含 cases、
quality calibration 与 6 份 cassette。
