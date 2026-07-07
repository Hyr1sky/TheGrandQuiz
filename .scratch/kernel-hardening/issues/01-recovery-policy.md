# M6-01 — kernel RecoveryPolicy + ErrorClass（错误分类统一裁决）

Status: done（merge 至 main 5f1bbf6；五门全绿 310 passed；SKELETON #7 销账，src/ 计数 4；kernel↛domain lint-imports KEPT）
Type: AFK

> 终审记：4 路对抗验证有一处 correctness "blocking"（pytest 间歇红）实为**验证脚手架伪报**——4 路
> verify 共用同一 worktree，mutation 路（去 DEGRADED 标/删事件）的 sed/checkout 与 correctness 的全量
> pytest 并发，撞出间歇失败；隔离后 45/45 全绿、合并后 main 上 310 passed ×3 稳绿。真代码无 flake。
> 非阻塞 concern：GradingError 的 skip 路仅经 QuestionError 间接测（同 DEGRADED 路，classify 已双测覆盖）。

## Parent
[PRD: 宽口径 kernel 加硬](../PRD.md)

## What to build

把散落的错误处理收编为 kernel 级 `RecoveryPolicy` + `ErrorClass` 分类法（architecture 搭建顺序 step 6）。
关键约束：`kernel/recovery.py` **禁止 import domain**（issue 03 的 import-linter 门会挡）——故不能 `isinstance`
domain 异常。改用**异常自带分类**：domain/providers 异常 import kernel 的 `ErrorClass` 给自己打 `error_class` 标；
kernel policy 读该属性分类，**未分类 → 默认 FATAL（大声失败）**。CLI `run_quiz` 改用 policy 裁决、删 SKELETON #7。

## Acceptance criteria

- [ ] `kernel/recovery.py`：`ErrorClass` 枚举（至少 `FATAL` / `DEGRADED`；可含 `TRANSIENT`/`RESOURCE_UNREADABLE` 前向保留，但只实现有真实映射的行为，**不留死分支**）+ `RecoveryPolicy.decide(exc) -> Decision`（`Decision` 表达 propagate / skip 等）。分类靠读 `exc.error_class`（Protocol/属性），未带 → `FATAL`。
- [ ] `kernel/recovery.py` **零 import domain**（`uv run lint-imports` 必须绿——这是本 issue 的硬门之一）
- [ ] domain 异常自标：`QuestionError`/`GradingError` → `DEGRADED`；`FetchError`/`ReaderError` → `RESOURCE_UNREADABLE`（或前向保留类）。它们 import kernel 的 `ErrorClass`（domain→kernel 合法方向）。
- [ ] `ReplayMiss` → `FATAL`（显式标或依赖未分类默认；二选一），policy 对它必 propagate、**绝不 skip**（决策6 不可破）
- [ ] 未知/未分类异常 → `FATAL`（fail loud）
- [ ] recovery 决策发成 kernel `AgentEvent`（新 `EventType`，如 `RECOVERY_DECIDED`）上脊柱，payload 含 error / error_class / decision
- [ ] `RecoveryPolicy.decide` 纯确定：无墙上时钟 / random（determinism/replay 安全）
- [ ] CLI `run_quiz`：删硬编码 `except (QuestionError, GradingError)` + `SKELETON(M6)` 注释，改用 `policy.decide`（DEGRADED→跳过本轮、其余→冒泡）；行为等价或更强
- [ ] `assess_once` **签名/逻辑一行不改**（仍原样 raise；eval harness 里 ReplayMiss 照样硬失败）
- [ ] `docs/skeleton-ledger.md` 的 #7 标 ✅ done（并核对 grep SKELETON 计数）
- [ ] TDD：ReplayMiss→propagate（mutation：误标 DEGRADED → 测试红）；QuestionError→skip；未知→propagate；decide 确定性；recovery 事件上脊柱
- [ ] 五门全绿（含 `uv run lint-imports`）

## Files (owner)
新 `src/grandquiz/kernel/recovery.py`、`kernel/events.py`（加 `RECOVERY_DECIDED` EventType）、
`domain/learning/question.py`+`grading.py`+`reader.py`+`fetch.py`（各加 `error_class` 标，import kernel ErrorClass）、
`providers/replay.py`（ReplayMiss 标 FATAL，可选）、`interfaces/cli/app.py`（run_quiz 改用 policy）、
`docs/skeleton-ledger.md`（#7 销账）、新 `tests/test_recovery.py`（+ 必要时补 CLI/domain 测试）。

## Blocked by
None（Phase 0 已 merge 至 main e538ef8：import-linter 门 + preference.py 已在）。串行下一步是 M4，不与本 issue 并行。
