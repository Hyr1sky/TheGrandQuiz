# 48. Eval E3–E4：类型化执行证据与 Replay 资产所有权

## 为什么继续深化

E0–E2 已把 Harness 的执行、suite policy 与报告投影分开，但仍留下两类隐式契约：

- `SolveResult.context: dict[str, Any]` 以字符串键连接 solver、grader、runner 和测试；字段拼错、类型漂移、
  无消费者字段都不能由 Pyright 发现。
- case16/case17 的 Acquisition cassette、adapter 与 fingerprint 分散在 solver 常量、YAML、录制脚本和
  发布测试；配置名称表达通用 fixture，运行时却暗中绑定某一 case 的资源。

这两项都会让 Eval 自身产生假绿风险，因此分成两个独立竖切处理；没有重录或修改 cassette。

## E3：类型化 per-kind observation

`SolveResult.context` 被四种不可变 observation 替换：

- `AssessObservation`：选题基线、精确 scope、薄弱前置和会话内已问题目；
- `BasicIngestObservation`：普通 ingest 的证据已完全由 result/store/events 表达，不再保存冗余字段；
- `WebAcquisitionObservation`：成功/拒绝 URL、拒绝结果及失败前 provider 调用快照；
- `ReactObservation`：最终用户可见输出与 grounded document 基线。

`SolveResult` 在构造点验证 Case 与 observation 的合法组合。grader 和 Tier-2 runner 通过字段访问与
`isinstance` narrowing 读取证据，删除了全部字符串 key、`Any` 和相关 `cast`。`item_ids` 改为从 `items`
派生；无人消费的 `scope`、`approved_concepts` 不再写入。

## E4：Replay 资源由行为用例拥有

新增严格的 `AcquisitionReplayProfile`，由 case16/case17 YAML 声明：

```text
cassette
search_adapter
search_fingerprint
fetch_fingerprint
normalization_version
```

solver 与 case14/15/17 录制脚本都读取正式 `load_cases()` 结果，不再复制 case messages、cassette 文件名或
case17 指纹。Tier-2 cassette 作为 suite-owned asset 由单一常量声明。

发布资源测试不再手写“应有六个文件”的孤立清单，而是比较：

```text
包内实际 fixture 集合
== ReactCase cassette
 + case-owned Acquisition cassette
 + suite-owned Tier-2 cassette
```

因此新增无人认领的文件、删除仍被引用的文件、缺失 acquisition profile，以及修改 profile 后继续命中旧
cassette，都会大声失败。日期型 case17 fingerprint 为保持既有 Replay 保留；下一次真实重录可在唯一 YAML
声明处升级为语义版本。

## 验证

- `python -m grandquiz.evals`：17/17；
- Python：1086 passed；
- Ruff、format、Pyright、import-linter：全绿；
- kernel 分层守卫保持 147 files / 657 dependencies、0 broken；
- 六份包内 Eval fixture 集合与声明所有者精确一致；cassette 字节未变。
