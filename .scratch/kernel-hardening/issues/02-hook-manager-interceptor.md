# M4-02 — kernel HookManager（interceptor 半边：before_* 改参 / 阻断 + 异常隔离）

Status: done（merge 至 main 1c2b29a；五门全绿 321 passed ×3 无 flaky；kernel↛domain KEPT；runner/assess_once 空 diff）
Type: AFK

> 终审记：单串行验证员（吸取 M6 并发共享 worktree 的假报教训）——4 类 mutation(MUT-A 丢弃改写值 /
> MUT-B 吞 veto / MUT-C 破坏 fail-closed / MUT-D 去 observer 隔离)各杀对应测试后撤销，最后单独跑门、
> pytest 连跑 2 次无 flaky。设计：run_before 按注册序折叠 interceptor；HookVeto 表阻断；非-veto 异常
> → fail-closed（转 HookVeto + 记录 + 发 HOOK_INVOKED 留痕，不静默放行）；reader 注入中和改注册式
> interceptor，组装点在 ingest.py。ApprovalGate reroute / runner 挂点 / after_turn 写入 hook 按 scope 未做。

## Parent
[PRD: 宽口径 kernel 加硬](../PRD.md)

## What to build

补齐 Hook 体系的 interceptor 半边（architecture.md:78-85）。observer 半边已在 `EventSink`（subscribe/register +
异常隔离）。新增 kernel `HookManager` 提供 **interceptor（`before_*`：可 transform 传入值、可 veto/阻断）**，
并把**注入防护**（当前 `reader.py` 内联的 `neutralize_fence`）改成注册的 `before_*` interceptor（证明"改参"，
且落在真实不可信内容边界）。分层同 M6：`kernel/hooks.py` **零 import domain**（import-linter 门），domain 侧
注册 hook（domain→kernel 合法方向）。

## Acceptance criteria

- [ ] `kernel/hooks.py` `HookManager`：注册 interceptor（按 `before_*` 命名点）+ observer；`run_before(point, value) -> value`（每个 interceptor 可返回改写后的值或发 veto 信号阻断）。**零 import domain**（`uv run lint-imports` 绿）。
- [ ] **异常隔离 + fail-safe**：interceptor 抛异常不炸 turn（隔离 + 发事件），且**不静默放行**——veto/安全型 hook 失败按 fail-closed（阻断 / 冒泡），沿用 M6"未知即 FATAL、fail loud"哲学。observer 抛异常同 EventSink 隔离（只读、continue）。
- [ ] hook 调用上事件脊柱（新增 kernel `EventType`，如 `HOOK_INVOKED` / 或 `INTERCEPTOR_APPLIED`），payload 含 point / 是否改写 / 是否 veto。
- [ ] **真客户**：`reader.py` 深读前经 `HookManager.run_before("untrusted_read", content)` 应用注入中和 interceptor（替代内联 `neutralize_fence` 直调；`neutralize_fence` 逻辑可复用为该 interceptor 的实现）。行为等价：不可信内容仍被中和。
- [ ] **veto/阻断能力单测**：合成一个 veto interceptor，断言它能阻断一次操作（证明"可阻断"这半能力，即使 production 审批门本轮不 reroute）。
- [ ] 确定性：HookManager 无墙上时钟 / random；interceptor 顺序确定（注册序）。
- [ ] **不碰 runner**（注入 interceptor 在 reader 触发）；`assess_once` 签名不变。
- [ ] TDD：改参（中和生效）/ veto（阻断生效）/ 异常隔离（坏 hook 不炸 turn 且 fail-safe）/ 顺序确定 / 事件上脊柱，各 mutation 可杀。
- [ ] 五门全绿（含 `uv run lint-imports`）。

## Out of scope（本 issue 不做）
- production 审批门（`ApprovalGate`）reroute 成 hook：与延后的 #3 suspend/resume 纠缠，留后。
- runner 的 `before_model` / `before_tool` 挂载点：无 tool 循环，等 ReAct。
- Preference Memory 的 `after_turn` LLM 写入 hook：属 ReAct 阶段（需自由对话信号）。

## Files (owner)
新 `src/grandquiz/kernel/hooks.py`、`kernel/events.py`(+hook EventType)、`domain/learning/reader.py`(改用 HookManager)、
注册处（`interfaces/cli/app.py` 或 ingest 组装点）、新 `tests/test_hooks.py`（+ 必要 reader 测试）。

## Blocked by
None（Phase 0 + M6 已在 main f427400）。串行：M4 之后是 M5 ContextBuilder。
