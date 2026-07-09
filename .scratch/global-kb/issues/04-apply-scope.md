# GKB-S4 — `apply_scope` + `resource_ids` scope 参数（修 #1 考错库）

Status: done（merge 至 main `f7cc8e1`，五门全绿 433 passed；apply_scope 纯函数 + resource_ids scope + empty_scope 拒答，5 mutation 全杀、默认路径 cassette 逐字节保留。scope-honor（30 seed 全落 scope 内）+ empty_scope（0 LLM 调用）已测。多资源 eval 用例留 S7。）
Type: AFK（多资源 eval 用例属 GKB-S7；本 slice 走单元 + 事件断言）

## Parent
[PRD: 全局 KB 重构](../PRD.md)

## What to build

给考核加**目录式 scope**：`start_quiz` / `assess_once` 接一个可选 `resource_ids`，代码按 **exact resource_id**
把候选池收窄到指定材料再选题；命中为空**诚实拒答**而非静默考别的库。这修 **#1 考错库**——语义匹配是 LLM 的活
（S3 目录 + S5 工具 description 让它填对 id），本 slice 只做代码侧的**确定性精确过滤**，不含任何模糊子串匹配。

## 锁定设计（不留给实现猜）

- **纯函数 `apply_scope(items, resource_ids)`**（落 `selection.py`，走 TDD）：
  - `resource_ids is None` → **恒等返回** `items`（默认全库；字节等价旧行为）。
  - 否则 → **保序**过滤出 `item.resource_id in set(resource_ids)` 的 item（保 item_id 升序，绝不重排序——
    重排即破 `rng.choice` 下标稳定）。
  - `select_target` 签名及其既有 caller **零改**：scope 是 select_target **之前**的上游预过滤。
- **`assess_once` 接 `resource_ids: list[str] | None = None`**：`items = apply_scope(store.all_items(), resource_ids)`
  → 判空 → 选题。判空分两支：
  - `resource_ids is None` 且全库空 → 既有 `ASSESSMENT_REFUSED(reason="empty_kb")`（case2 逐字节不动）。
  - `resource_ids` 非 None 且过滤后为空 → **新** `ASSESSMENT_REFUSED(reason="empty_scope")`（在 select_target 之前、不调任何 LLM）。
- **`start_quiz`**：`_StartQuizParams` 加 `resource_ids: list[str] | None = None`；handler 透传 assess_once。
- **scope 上事件脊柱**：`ASSESSMENT_STARTED` payload 带**有效 resource_ids + 命中数**（供 trace/eval 断言"考了哪个库"）。
- **确定性**：exact-id 过滤、无模糊匹配、无 clock/random；`resource_ids=None` 默认 → 现有单测/eval/cassette 字节不变。

## Acceptance criteria

- [ ] `apply_scope(items, resource_ids)` 纯函数：None 恒等、非 None 保序 exact-id 过滤；`select_target` 签名零改
- [ ] `assess_once` 接 `resource_ids`（默认 None）；`start_quiz` 接 `resource_ids` 并透传
- [ ] `empty_scope` 拒答：非 None scope 过滤后为空 → `ASSESSMENT_REFUSED(reason="empty_scope")`（在选题前、不调 LLM）；`empty_kb` 语义保留
- [ ] scope 上 `ASSESSMENT_STARTED` payload（有效 resource_ids + 命中数）
- [ ] **scope-honor 行为**：`resource_ids=[A]` → 所有出题 item 的 resource_id ∈ {A}（事件断言）
- [ ] TDD：apply_scope × focus(mixed/new/weak) × weak × asked 组合、None 恒等、空命中、保序，各 mutation 可杀
- [ ] `resource_ids=None` 默认路径：既有 8+2 eval + cassette 字节不变
- [ ] 五门全绿（含 lint-imports）

## Files (owner, 可能漂)
`domain/learning/selection.py`(apply_scope)、`domain/learning/assessment.py`(resource_ids + empty_scope)、
`domain/learning/tools.py`(_StartQuizParams + 透传)、`domain/learning/events.py`(如需 scope 事件常量)、
`tests/test_selection.py`、`tests/test_assessment.py`。

## Blocked by
GKB-S2（assess_once 签名重塑后再加参数，避免撞车）。可与 GKB-S3 / GKB-S5 并行。
