# PRD：窄口径卫生收口（Narrow Close-out）

Status: done（4 条 merge 至 main eb6d650/c234467/a31e8d5/24b00fd；五门全绿 297 passed 0 skip；未 push）
Triage: ready-for-agent

## Problem Statement

自评（2026-07-07 全仓审视 + 两道对抗评审）暴露：几处"声称 done"其实不诚实——
（1）`SqliteLearningStore` 静默丢 `LearningTask.language`（真 bug，假绿测试掩盖，反讽地抵消了 M8-01 语言修复）；
（2）`M7` 标 done 但 Preference Memory 零代码；（3）分层护栏 `kernel↛domain` 只靠 grep+约定，无自动门；
（4）文档债（`8 条`用例实为 10、"LLM judge" 半兑现宣称、遗留 `repl.py`）。竖切收口、扩 ReAct 之前，先把这些诚实收口。

## Solution

四条互不相交、可并行的 AFK 增量，让所有"声称 done"为真，并把 Preference Memory（语言偏好）从零建到可用。
用户决定：**语言作为第一个偏好（显式设置）**；难度偏好走行为推断，因当前 CLI 是确定性选择题、无自由对话/开放答题，
无推断信号——**难度偏好推断延后到 ReAct 阶段**（见 kernel-hardening backlog）。

## User Stories

1. 作为用户，我用非中文建 task 并落库重开后，题目仍是那门语言——不被静默退回中文。
2. 作为开发者，我信任"dict↔SQLite 两实现逐字段等价"这一被反复主张的不变量为真（含 `language`）。
3. 作为用户，我能显式设"出题用英文"这个偏好，它在出题时覆盖 task 默认语言（Learning + Preference 双记忆都喂考核）。
4. 作为开发者，偏好跨会话留存（重开仍生效），且 dict↔SQLite 双实现逐字段等价（含 `confidence`）。
5. 作为维护者，`kernel↛domain/interfaces/evals` 由 CI 的 import-linter 自动强制，回归即红——不再靠 grep。
6. 作为 reviewer，文档不撒谎：用例数、Tier-1/Tier-2 兑现度、无遗留死界面。

## Implementation Decisions

- **四条 issue、文件互不相交 → 并行 worktree 安全**（下方 owner 划分即冲突护栏）：
  - 01 tasks.language 列：owner `store.py` + `learning/migrations/0002_*.sql` + sqlite 持久化测试
  - 02 Preference Memory：owner **新文件 `preference.py`** + `learning/migrations/0003_*.sql` + 消费点
    `assessment.py`/`question.py` + **`app.py`（含顺手清掉 app.py:10 的 `repl.main` 残留 docstring）** + `test_preference.py`
  - 03 import-linter：owner `pyproject.toml`/`.importlinter` + `.github/workflows/ci.yml`
  - 04 文档债：owner `evals/__init__.py` + 删 `repl.py`（不碰 `app.py`——那句 repl 残留归 02 清）
- **迁移号预分配**：01=`0002`、02=`0003`（同一 learning.db 序列，避免并行撞号）。
- **语言优先级**：偏好（若设）> task 默认语言 > 系统默认（中文）。
- **confidence 字段现在恒 1.0（显式设置）**；推断器（confidence 累积）延后。

## Testing Decisions

- 只测外部行为：往返保真、优先级覆盖、跨会话留存、契约自动门咬合。
- **每条都要 mutation 可杀**：01 断"非中文往返存活"（删列/删读即红）；02 断 dict↔SQLite **逐字段**含 confidence（学 01 的教训，别再漏字段）+ 偏好覆盖优先级；03 故意加一条 `kernel→domain` import 验证门变红（验证后撤销）；04 grep 无残留 `8 条`/`repl`。
- 确定性纪律：02 禁 clock/random 泄漏，走注入。
- 现有分工延续：确定性核心 TDD；LLM 槽不动。

## Out of Scope

- 难度偏好 + 偏好推断器（confidence 累积）→ ReAct 阶段（需自由对话/开放答题的行为信号）。
- 凭 token suspend/resume（审批门 #3 / Responder #6）、kernel/subagent.py 提取（#4）→ 见 kernel-hardening / 后续。
- 真实 fetch httpx/超时、Tier-2 LLM judge、golden cassette 扩充 → 可选，本轮不含（列入 kernel-hardening backlog 备选）。

## Further Notes

自评基线 HEAD `b5849ef`。四门 CI：`ruff check && ruff format --check && pyright && pytest`（03 后 +import-linter）。
