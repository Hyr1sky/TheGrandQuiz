# 50. Eval-guided Evolution E8：反馈提案、人工晋升与回滚门

## 为什么最后一段不能是“自动改 Prompt”

E5–E7 已经能回答评测覆盖、完整 Subject Identity、语义质量与 baseline/candidate 配对差异，但这些证据仍
不能授权系统改生产配置。真实 verdict correction 是已见反馈；Development Gold 是已经用于设计或校准的
数据；Replay 只证明可复现。若把其中任一项直接当晋升依据，就会把过拟合包装成自进化。

E8 因此交付的是一个人类拥有的控制面，而不是 autonomous optimizer：系统可以从已审批反馈形成候选、
自动运行 Development Eval 并给出 paired report；是否继续、何时揭晓新 Holdout、是否激活以及何时回滚，
都需要显式人类命令。

## Approved Feedback → Change Proposal

`grandquiz.evals.proposal` 接受两类已审批来源：

- active + approved 的 Eval Inbox candidate；
- 人类明确批准的 Experiment failure slices。

VerdictCorrection 投影始终标为 `exploratory` 且 `release_holdout_eligible=false`；已揭晓的失败切片属于
`development_gold`。两者都只能解释“为什么值得提出候选”，不能充当盲测证据。

一次 `ChangeProposalRequest` 只能修改一个已经存在的 prompt 或 policy binding，并必须声明：

- exact baseline subject id 与 expected base binding；
- Eval Surface、目标 key、candidate version；
- draft content、rationale 和 approved provenance；
- 可选的 superseded proposal id。

确定性代码拒绝未知 target、stale version、跨 surface 证据和 secret-shaped content。内容 SHA-256 被写入
candidate binding，使“同版本名、不同内容”不会得到同一 Subject Identity。Proposal ledger 追加而不覆盖：
相同 request 完全幂等，supersede 通过新记录引用旧 proposal，历史保持可审计。

Proposal 本身没有生产写接口。它必须通过 `bind_proposal_experiment()` 绑定 E7 的 exact
`PairedEvalExperiment`；baseline/candidate 任一 subject identity 不符都会 fail closed，因而不存在绕过正式
实验契约的 proposal-only shortcut。

## Development Eval → Human Decision

`grandquiz.evals.promotion` 将 paired experiment report 投影为三种显式决定：

- `reject` → rejected；
- `keep_experimental` → experimental；
- `accept` → eligible-for-holdout。

即使 Development report 是 `eligible_for_review`，accept 也不会改变 active subject。Promotion ledger 只保存
proposal/report/subject 的安全身份、报告哈希、actor 和 reason hash，不包含 prompt、私有样本、cassette 或
原始 trace。报告状态不是人类意图；没有 `HumanPromotionDecision` 就不能进入激活接口。

## Release Holdout 与不可逆揭晓

`freeze_release_holdout()` 只接受全部由已审批 blind labels 组成、`eligible_blind_count == candidate_count`、
`exploratory_count == 0` 的不可变 Dataset Snapshot，并要求显式确认尚未揭晓及预注册 threshold policy。
它还必须与 Development Gold snapshot 不同。

Holdout 运行继续使用 exact baseline/candidate 的 paired experiment。结果一旦揭晓，无论通过还是失败，产物
都固定为 `evidence_class_after_reveal=development_gold`、`release_holdout_eligible=false`，因此不能重复冒充
新的 Release Holdout。只有满足预注册门的报告才能生成 passed result。

## Activation 与 rollback 为什么只是 Subject 选择

激活不是编辑 prompt 文件、Provider 配置或历史报告，而是追加一条 `SubjectSelection`：

```text
previous_subject_id → selected_subject_id
```

Activation 必须同时找到 ledger 中的 human accept、匹配 proposal 的 passed Holdout result，并确认 baseline
仍是 active subject。记录永远保留 exact previous identity。Rollback 只追加反向 selection 并恢复该 identity，
不会重写 decision、report、cassette、dataset 或任何历史 subject。decision、activation 和 rollback 的重复
request 都是幂等的，冲突 payload fail closed。

## 没有发生什么

- 没有真实候选被激活；
- 没有新 Release Holdout 被创建或揭晓；
- 没有修改生产 prompt、Provider binding、学习事实、Dataset Snapshot 或 cassette；
- 没有增加数据库迁移或 Web/CLI 管理页，因为当前尚无需要持久查询 proposal ledger 的真实消费者；
- 没有新增任何外部 LLM 调用。

第一次真实晋升仍是 HITL：维护者先批准 proposal/Development report，再准备全新的、隐私审批通过的未见
Holdout，最后显式发出 activation。这个限制是 E8 的产品语义，不是未完成代码。

## 验证结果

- Proposal/Promotion 直接契约测试：16 passed；
- Python 全量：1,140 passed；
- Ruff lint / format、Pyright strict、import-linter：全绿；
- import-linter 分析 157 files / 680 dependencies，`kernel` 分层守卫保持。

至此 E5–E8 已形成完整的受控回路：覆盖与身份 → 经人工校准的质量证据 → 不可变 paired experiment →
approved feedback proposal → 人类决定 → 新 Release Holdout → 可回滚 subject selection。下一产品主线可在
独立 spec 中推进 Material Channels，不需要依赖 proposal/promotion 内部模型。
