# v0.4.0 真实 Provider 发布 Dogfood

日期：2026-08-07  
结论：通过；未发现新的 P0/P1

## 为什么还要做这一轮

确定性测试证明代码在固定输入下可重复，但不能证明真实模型会遵守题型计划、产出可审批的知识点、正确
进入申诉生命周期，也不能证明真实搜索和网页质量门仍能协作。因此正式 push 前，用一次性 SQLite 和公开
自造材料跑一条窄竖切；不写维护者生产库，也不把正文、Prompt、模型输出或密钥提交进 Git。

本轮 basic 使用 `deepseek-v4-flash`、Thinking Off；enrich 使用 `qwen3.7-plus`、Thinking Off；材料发现
使用已配置 Tavily。安全执行身份来自 Provider 的公开审计投影，API Key 没有进入输出或 Trace。

## 结果

1. **本地上传与人工审批**：Reader 生成 7 个候选，人工模拟批准 6 个、剔除 1 个，最终写入 1 个
   resource / 6 个 KnowledgeItem。Trace：`43e14f98739e40478a3c2debb5d491fd`，1 次模型调用，
   2,004 Token。
2. **混合题型计划**：显式要求“选择题、选择题、简答题”，真实结果严格投影为“选择题、选择题、开放”，
   没有回到三道选择题。Assessment Trace：`26a5926b72384160a23d83d815a63051`。
3. **开放题与申诉**：初答为“错”，判卷返回 2 个逐点评判和参考答案；一次补充后改判为“对”。数据库中
   原答逐字保持不变，补充说明单独保存，申诉 span 与整个 Trace 都以 completed 闭合。该 Trace 共 5 次
   模型调用，4,647 Token。
4. **材料发现审批边界**：Tavily 返回 5 个候选，5 个均可审核；搜索结束时 KB 资源数不变。模拟拒绝 1 个、
   批准 1 个后，批准项进入 Acquisition 并停在 `needs_input` 等待知识点二次审批。Discovery Trace：
   `1c71cd56aa9440f685efa55b62bcfe83`；Acquisition Trace：`3ca807fd789c45679d0dcc536ce7f841`，
   1 次 Reader 调用，14,474 Token。
5. **Eval 人工授权闭环**：申诉产生的 Verdict Correction 同步为 1 条 exploratory 候选；审核后生成
   Snapshot `0df51c1085e7f67d8be2d1a0395da05d3d75462d5c7592e40fee2d8445cbc538`，其中 eligible 0、
   exploratory 1。关闭并重新创建应用后仍可读取该 Snapshot。
6. **低质量 URL 零污染**：`https://github.com/login` 以 `login_page / fetch` 结构化失败，没有新增 Resource
   或 KnowledgeItem。Trace：`6992a0c40d564c3b88c0fdd66044974d`；质量门在模型调用前拒绝，因此 Token 为 0。

本轮真实模型合计 7 次调用、21,125 Token。原始 SQLite 与 Trace 留在 gitignored 的
`localtemp/v040-release-dogfood-20260807/`，供 owner 本地复查；它们不是 Release asset，也不会进入提交。

## 发布判断

这轮证明的是 Provider 接线和五条人工授权链在真实非确定性输出下可以闭合，不会覆盖既有的 30 条
Development Gold 结论。自动判卷三值一致率仍为 83.33%，低于 85% 策略晋升门；真实 dogfood 通过不等于
模型已经适合无人监督判卷。v0.4.0 仍以 Evidence、Trace 和用户申诉作为人工纠正边界。
