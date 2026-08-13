# 47. Eval Harness E0–E2：稳定入口，分离执行、评判与报告

## 为什么做

`evals/harness.py` 随 17 条行为用例、Tier-2 Quality Judge 和 HTML 报告逐步增长到约 1470 行。问题不只是
文件长，而是三种变化原因揉在一起：增加被测 workflow 会碰 solver，调整质量门会碰 suite runner，修改报告
页面也会碰同一文件。CLI、录制脚本与测试又都依赖 `grandquiz.evals.harness`，直接改 import 会扩大迁移半径。

## E0：先冻结外部行为

新增 facade conformance test，固定：

- `load_cases / solve / run_case / run_all / render_report / export_html_report` 继续可从 `harness` 导入；
- 17 个 case 的稳定顺序、kind 与唯一 Tier-2 `grounded_answer` 归属；
- 既有测试继续覆盖 ReplayMiss 硬失败、17 个详情文件、Quality 子报告、自包含 HTML 和成本分列。

没有冻结整页 HTML 字节或内部 helper，避免 characterization test 阻碍合法重构。

## E1–E2：按职责形成深 Module

```text
cases/*.yaml
    ↓
solvers.py   ── Case → SolveResult；确定性 fixture / Replay / 真实 workflow
    ↓
runner.py    ── Tier-1 + 校准优先 Tier-2 + 硬失败 → CaseReport
    ↓
reporting.py ── CaseReport → 文本 / 自包含 HTML（纯投影）
    ↑
harness.py   ── 兼容 facade + CLI main
```

- `harness.py` 从约 1470 行降到 59 行；既有调用者无需修改。
- `runner.py` 不认识 HTML，`reporting.py` 不认识 solver 装配或 suite 执行，`solvers.py` 不决定质量 gate。
- `CaseReport` 与 `SolveResult` 集中在 `result.py`，成为 runner/grader/reporting 的共享契约。
- `solvers.py` 仍约 900 行，但只有“被测执行路径变化”一种理由；在共同变更历史尚未证明 per-kind 子包前，
  不为行数继续拆出浅层文件。

## 保留到 E3 的问题

`SolveResult.context: dict[str, Any]` 仍是规则 grader 的无类型数据袋。它是下一项真正的语义耦合，但替换它会
改变 grader Interface，必须用 typed per-kind observation 单独推进，不能作为 E0–E2 的机械附带改动。

## 验证

- `python -m grandquiz.evals`：17/17；
- Python：1078 passed；
- Ruff、format、Pyright、import-linter：全绿；
- kernel 分层守卫保持 147 files / 656 dependencies、0 broken。
