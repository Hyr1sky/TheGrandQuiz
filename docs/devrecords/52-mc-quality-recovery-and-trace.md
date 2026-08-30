# 选择题质量门、恢复与 Trace 补丁

日期：2026-08-29

## 现象与诊断

真机 Trace `b7a9555daff1430593b86530c5be986d` 并不是“模型偶尔没回答”，而是同一个薄材料知识点在最高
难度下被要求生成 6 个选项，并让 5 个干扰项全部达到“合理干扰”。一次不合格就整题重做，最终形成 5 次
出题、9 次干扰项评审、约 8.9k tokens 与约 28 秒延迟，仍以 `QuestionError` 结束。

SQLite 截图里的 `seq` 跳号则是另一件事：表格按全局插入行展示，不同 `trace_id` 的事件会交错；`seq` 的
契约是“同一 trace 内从 0 连续递增”，不是整张表只有一条全局序列。对 Aug29 数据逐 trace 核验后没有发现
缺号，本次增加了两个 emitter 交错落库的回归测试，把这个边界钉死。

## 修复后的生成策略

高档选择题统一为标准四选一，不再用 5/6 个选项制造表面难度。当前明确映射为：tier 1/2 三选一，
tier 3/4/5 四选一；传入目标数后是“恰好 N 项”的硬契约，模型多给或少给都会被拒绝。质量门改成集合契约：

- Tier 4：每个干扰项至少为“较弱干扰”，其中至少 1 项为“合理干扰”；
- Tier 5：每个干扰项至少为“较弱干扰”，其中至少 2 项为“合理干扰”；
- 默认和低档不启用实时 judge，避免为普通题额外付费。

当某个干扰项不合格时，系统冻结题干、正确项、`answer_index`、Evidence 和已经通过的干扰项，只要求模型
替换必要的坏项。同一文本的 judge 结果在单次生成任务内缓存，因此修复一项不会重新评审全部选项。生成仍是
最多 3 次的有界过程，没有把问题隐藏在更大的重试次数里。即使三个干扰项全部失败，冻结契约仍成立：
retained 集合可以为空，但题干、正确项、下标和 Evidence 不能被下一次修复改写。

```text
首次四选一
  → 评审 3 个干扰项
  → 保留通过的 2 项
  → 只替换失败的 1 项
  → 只评审新选项
```

定向测试中的“一项失败”场景由 2 次生成、4 次 judge 完成；若仍采用两轮全量重评，同样场景需要 6 次
judge。这个数字是确定性测试结果，不是新的真实模型性能结论；真实成功率与延迟仍需下一轮 dogfood 验证。

## 恢复语义

CLI 原本已经通过 `RecoveryPolicy` 把 `QuestionError` 解释为“跳过本题”，Web 却把所有异常都投影成整场
`failed`。本次让 Web 复用同一分类：

- `DEGRADED`：进入可恢复态，用户可“重试本题”或“跳过此题”；
- `FATAL`：仍然结束整场并标记失败，避免吞掉 Provider、Replay 或程序错误；
- 最后一题若选择跳过，考核正常进入 `completed`，不会永远卡住。

这里的操作按阶段收窄：只有 `question_generation` 允许重试生成；`grading` 失败已经接收过用户答案，只允许
跳过，不会悄悄重生成另一题并丢掉提交。出题失败重试只在 workflow 完整返回后才消费随机 seed，因此保持同一
目标 KnowledgeItem，而不是把“重试本题”实现成换题。

重试与跳过都带幂等 `request_id`。前端在成功收到重试响应后会释放旧 id，使用户下一次主动重试代表一个新
意图；若网络响应丢失，则保留原 id，重发不会重复启动任务。

## Trace 如何变得可定位

每次选择题生成现在都有独立的 `learning.multiple_choice_generation` span，模型出题与干扰项 judge 是它的
子 span。名称明确限定为选择题，避免让尚未采用同一拓扑的开放题在观测中被误认为已经覆盖。
事件记录目标选项数、质量策略、尝试次数、局部修复次数、judge 调用数和稳定的拒绝原因码，例如
`invalid_json`、`option_count_unmet`、`distractor_quality_unmet`、`repair_contract_violated`。

结构化输出耗尽才记录最后一个稳定 `reason_code`；Provider 或 judge 自身异常只记录安全的 stage/error class，
不再被错误标成 `invalid_output`，避免污染后续 Eval 和故障统计。

质量门耗尽时会依次留下 generation failed、`recovery.decided`、`error` 与
`web.assessment_run.degraded`。Observatory 将它投影为 `waiting_input`，表达“等待用户重试或跳过”，而
不是错误地长期显示 running，也不会提前伪造终态。

## 本次没有做什么

本补丁没有加厚 `KnowledgeItem`，也没有引入知识关系、CanonicalConcept 或自动扩展材料。薄 Evidence 下
很难出高质量连锁题是上游内容模型问题，但在有真实消费者与 Eval 证据前，不应把它混进故障恢复补丁。下一步
应单独讨论“可考察单元”如何从单个 item 组合 Evidence、邻近概念与题型需求。

## 收口验证

本轮先由测试钉住失败语义，再实现局部修复与阶段化恢复，最后做双轴审查。审查发现并修正了四类容易被
“测试全绿”掩盖的问题：选项数必须恰好匹配、重试不能偷偷换 KnowledgeItem、判卷失败不能伪装成出题失败、
Provider/Judge 异常不能被错误统计为结构化输出无效。

最终本地门禁结果：

- Python：`1146 passed`；Pyright strict、Ruff lint/format、import-linter 全部通过；
- Eval：内置 `17/17` case 通过；
- Web：Vitest `78 passed`，Playwright `23 passed, 1 skipped`，静态构建与 OpenAPI 无漂移；
- Sites：`4 passed`；
- Package：sdist/wheel 构建成功，wheel 内 Eval 资产和 Web 静态资源完整；隔离安装后 CLI、报告、health API、
  首页和 `/study/session` 深链均通过 loopback 冒烟。

这些数字只证明补丁没有破坏既有契约，并不等价于真实模型下的选择题成功率提升。后者仍需用同一批材料做
paired dogfood，至少比较成功率、平均生成尝试、judge 调用、token、延迟和降级率。
