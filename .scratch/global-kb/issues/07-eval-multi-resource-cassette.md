# GKB-S7 — eval 多资源夹具 + scope/empty_scope/题型 三类新用例 + 真机重录

Status: ready-for-agent
Type: AFK（cassette 我用配置好的真 key 录；真机体验质量属你 dogfood）

## Parent
[PRD: 全局 KB 重构](../PRD.md)

## What to build

把全局 KB 的三个新行为钉进 eval harness（Tier-1 规则断言）：**scope-honor**、**empty_scope 拒答**、
**question_type-honor**。为此把 `build_stocked_store` 扩成**多资源夹具**，并录制新 item×题型组合所需的 cassette
条目（用已配置的真 key，别伪造）。这是全局 KB 的可回归保障——让"考错库/错题型"这类 bug 一旦复发就被 eval 抓住。

## 锁定设计（不留给实现猜）

- **多资源夹具**：`build_stocked_store` 扩出 ≥2 个不同 topic 的 resource（各含若干 item），供表达"只考其中一个
  resource"。natural 基线仍与生产同源（`all_items()`），确保跨资源 case 对正确基线打分。
- **新 eval 用例（YAML + 规则 scorer）**：
  - **scope-honor**：请求 `resource_ids=[A]` → 断言所有 `QUESTION_ASKED.item_id` 的 resource_id ∈ {A}（不串到别的 resource）。
  - **empty_scope**：请求一个无匹配的 scope → 断言 `ASSESSMENT_REFUSED(reason="empty_scope")`、不出题、不调判卷。
  - **question_type-honor**：请求 `question_type="简答"` → 断言 `QUESTION_ASKED.effective`=开放、**不出选择题**。
- **cassette**：新 item×题型组合的出题/判卷条目用 record 脚本对真 provider 录（enrich 出题 / basic 判卷）；
  与既有 2 条 golden 分开对待、**严禁误碰既有条目**。MC 判卷是代码、无需 cassette。
- **既有用例不动**：现 8+2 用例默认路径（不传 scope/题型）逐字节保持；新用例独立挂载。
- **确定性**：规则 scorer 走事件断言（item_id/resource_id/effective/reason），非 LLM-judge（那是 R3）。

## Acceptance criteria

- [ ] `build_stocked_store` 扩多资源夹具（≥2 topic）；natural 基线与生产 `all_items()` 同源
- [ ] scope-honor 用例：`resource_ids=[A]` → 全部出题 item ∈ A（规则 scorer 绿）
- [ ] empty_scope 用例：无匹配 scope → `ASSESSMENT_REFUSED(empty_scope)`、零出题/判卷
- [ ] question_type-honor 用例：`question_type="简答"` → effective=开放、不出选择题
- [ ] 新 cassette 条目真机录制（真 key，enrich/basic 两槽）；既有 2 条 golden 未被误碰
- [ ] 既有 8+2 用例默认路径字节不变
- [ ] 五门全绿（含 lint-imports）；eval report 跑通
- [ ] 交你 dogfood：真机 `grandquiz react` 走"自然语言选材料 + 定题型"完整体验

## Files (owner, 可能漂)
`evals/harness.py`(build_stocked_store 多资源)、`evals/cases/*.yaml`(三类新用例)、`evals/graders/rules.py`(如需新 scorer)、
`tests/fixtures/*.cassette.json`(新条目)、`scripts/record_assess.py`(录制)。

## Blocked by
GKB-S4、GKB-S5、GKB-S6（被 eval 的 scope/题型/端到端行为都要先在）。
