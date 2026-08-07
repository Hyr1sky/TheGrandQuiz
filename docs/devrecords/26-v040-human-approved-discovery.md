# v0.4 人工授权闭环：先形成候选，再让用户决定

日期：2026-08-01
状态：代码 RC 完成；首批真实人工盲标已通过本轮 inbox 形成 Snapshot，后续校准证据见
[27-real-grading-calibration-preparation.md](27-real-grading-calibration-preparation.md) 与
[28-grading-contract-and-model-pilot.md](28-grading-contract-and-model-pilot.md)。

## 为什么这一轮不直接做“自动数据飞轮”

v0.3 已经能产生两类有价值的信号：材料搜索结果和用户判决纠正。但“模型找到的内容”不等于“用户认可的
学习材料”，“用户纠正过模型”也不等于“这条数据可以公开、训练或作为盲标金标准”。v0.4 因此只解决授权：

```text
候选出现 → 人看到必要信息 → 明确批准或拒绝 → 进入已有的可信路径
```

它刻意不做定时任务、自动入库、自动上传、训练、主动推荐或新的学习策略。这样即使后续质量实验失败，
本轮仍然提供清晰可用的材料管理和本地 Eval 数据管理能力。

## 1. 材料发现只是收件箱，不是入库捷径

`MaterialDiscoveryService` 接受主题和有限 source policy，调用注入的 `SearchProvider`，然后确定性完成 URL
规范化、同批去重、已有资源识别和摘要最低质量检查。provider rank 被保留，但系统不伪造“相关性 87%”
之类没有校准依据的分数。

搜索成功或失败都会保存成 batch。Trace 只记录 query 指纹、adapter、候选数和稳定错误码，不记录原始主题、
URL 或摘要。服务重启后，Web 仍能从 `learning.db` 读取最近发现。

批准候选时没有复制一套抓取代码，而是调用既有 Acquisition：

```python
approved_candidate -> AcquisitionManager.start_url(control_token=...)
```

因此 SSRF 防护、异步状态、取消、错误信封和“候选知识点二次审批”全部保持原口径。浏览器在请求前保存
稳定 request id 和控制 token；即使响应丢失，重试也只会得到同一个 Acquisition，不会重复启动。批准事务
同时写入 activation outbox，事务提交后才由一个进程用 SQLite CAS 领取；领取成功后才 emit/schedule，其他
进程静默退出。若进程在领取前退出，重启会恢复 queued run；若 trace 或任务调度失败，run 会立即补偿为
failed，而不是留下一个永久 approved 但无法继续的候选。

## 2. Eval inbox 把“有反馈”与“可使用”分开

`EvalInboxLedger` 汇合两种输入：

- append-only 判决纠正投影：用户看过模型结果，固定为 exploratory；
- 用户显式导入的盲标样本：只有 `blind_to_model_output=true` 且标签完整时才 eligible。

相同 attempt 或 sample ID 出现新 payload 时，旧候选被标为 `superseded`，来源事实不被修改。Web 默认折叠
答案、题目和标注正文，用户展开检查后才能批准。只有 `active + approved` 候选可以生成快照。

快照 ID 是版本、脱敏 profile、规范化 items（含审核来源）的 SHA-256。相同输入永远得到相同 ID，历史快照不更新：

```text
snapshot_id = sha256(canonical_json(approved_items))
```

快照同时给出 `eligible_blind_count` 与 `exploratory_count`，避免把纠正样本混入发布质量 gate。它仍只存在
本地，不会自动 git add、上传或训练。

## 3. 这次如何避免职责继续变重

- `discovery.py` 拥有候选领域规则和 SQLite adapter，但不认识 FastAPI 或 Acquisition。
- `discoveries.py` 是应用服务，只负责把人工批准桥接到 Acquisition，并发 AgentEvent。
- `grading_samples.py` 保存共享样本契约，避免 domain 反向依赖 `evals/`。
- `eval_inbox.py` 只负责版本、审核和快照；独立 import receipt 固定每个 request 的完整 manifest，哪怕
  payload 与当前候选相同也不会丢失幂等证据；`eval_management.py` 负责事实投影与事件审计。
- 搜索环境配置移到 channel-neutral `interfaces/search_config.py`，Web 不再 import CLI composition。

这仍是一套适合个人 Agent 的窄结构：一个 migration、两个 inbox、两个应用服务，没有队列、调度器、通用
审批框架或独立数据平台。

## 4. 验收与可复述指标

- Python：`980 passed`；新增覆盖失败批次恢复、URL 去重、审核幂等、Acquisition 跨进程单次领取与激活失败
  补偿、候选 supersede、隐私门、快照不可变和 eligible adapter。
- Web unit：`48 passed`；覆盖敏感内容默认折叠、审批前按钮禁用和快照结果。
- Playwright：桌面/窄视口 `18 passed`；覆盖“发现不入库”和“隐私审核后快照”，第二视口还验证审核状态可恢复。
- Ruff format/lint、Pyright、Web lint/typecheck、OpenAPI 生成均通过；最终生产 build 与双轴审查见本次提交门。

下一步不是再加字段，而是把正在进行的真实盲标放入 inbox，固定一个明确 snapshot，再运行 v0.3 的生产
grader calibration。只有 gate 给出证据后，才讨论 v0.5 是否消费这些数据做更主动的学习策略。

该步骤随后已完成；第一次 gate 失败后的契约收窄、模型对照与新 holdout 要求见上述 27/28 记录。
