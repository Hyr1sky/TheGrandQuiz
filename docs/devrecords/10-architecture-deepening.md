# 四项架构 Deepening 开发记录

> 记录日期：2026-07-24
> 范围：`.scratch/architecture-deepening/` 的 AD-S1–S4 与收口审计。
> 目标：不改变考核行为、数据库 schema 和 Replay 契约，把调用者反复承担的知识收进更深的 Module。

## 1. 这里说的“深化”是什么

这次没有增加产品功能。用户看到的仍然是同一套 ingest、quiz、ReAct 和 eval，变化发生在代码内部的
职责分配上。

可以把一个浅 Module 想成“前台只负责转接电话”：真正办事需要调用者知道后面十几个部门、办理顺序和
异常规则。深 Module 则像一个办事窗口：调用者只说明目标，窗口内部掌握稳定依赖、状态和办理顺序。

本轮集中处理了四个信号：

1. CLI quiz 和 ReAct quiz 都在手工维护同一套多轮考核状态。
2. 五类 SQLite Adapter 共用一条连接，却由调用者逐个装配和关闭。
3. 三种 Eval case 共用一张大量可空字段的配置表，拼错配置可能静默走到错误 workflow。
4. 代码已经迁移到全局 KB、稳定 locator 和 ResourceRevision，部分当前态文字仍在讲旧模型。

它们的共同点不是“文件太长”，而是调用者知道得太多。深化的判断标准因此是：

- 调用者需要理解的规则是否变少；
- 同一个改动理由能否留在一个地方；
- 非法组合能否更早失败；
- 既有真实 Adapter、事件脊柱和确定性 workflow 是否仍然保留。

## 2. AD-S1：用 AssessmentSession 收住多轮考核状态

### 原来的摩擦

`assess_once` 是正确的单题确定性 workflow。它负责选题、出题、收答案、判卷和记账，不应该被改成自由
ReAct。但在它外面，CLI 与 `start_quiz` 都需要自己记住：

- store、provider、responder 和 memory；
- Preference Memory、AskedQuestions 与 Difficulty；
- 本场会话已经问过哪些题；
- 每一轮应该使用哪个确定性随机种子。

这意味着以后增加一项跨轮规则时，两个入口都可能需要同步修改。

### 深化后的 Interface

新增 `AssessmentSession`，由它持有一场考核中稳定不变的依赖和跨轮状态：

```python
session = AssessmentSession(
    store=store,
    provider=provider,
    responder=responder,
    memory=memory,
    seed=seed,
    asked_questions=asked_questions,
    preferences=preferences,
    difficulty=difficulty,
)

for _ in range(rounds):
    await session.assess(
        emitter=emitter,
        scope=scope,
        focus=focus,
        question_type=question_type,
    )
```

`AssessmentSession.assess()` 内部仍调用 `assess_once()`。因此本轮没有重写核心考核状态机，只是把
`recently_asked` 和 `seed + round_index` 这类“属于会话、却曾暴露给入口”的知识收了进去。

CLI 仍负责终端展示、`Ctrl+C` 和 `RecoveryPolicy`；ReAct tool 仍负责工具参数与错误投影。也就是说，
共用的是领域会话规则，不是把两个入口强行做成同一种界面。

### 得到的好处

- CLI 与 ReAct 不会逐渐长出两套去重和种子推进规则。
- 新增会话级依赖时，主要修改 `AssessmentSession`，调用者只负责装配。
- `assess_once`、Provider messages、事件顺序和 cassette 指纹保持不变。

## 3. AD-S2：让 LearningPersistence 真正拥有 persistence 生命周期

### 原来的摩擦

Learning Store、Learning Memory、Preference Memory、AskedQuestions 和 Difficulty 是五个不同领域
账本，这个职责划分本身是健康的。问题在于它们的 SQLite 实现共用同一个 `LearningDatabase`，生产入口
却要解包五个对象，并记住关闭五次。

更隐蔽的问题出现在一次判决的原子提交：代码通过 Adapter 的私有 `_learning_database` 属性猜测它们
是否属于同一个事务。这让 transaction seam 依赖实现细节。

### 深化后的 owner

`LearningPersistence` 只做三件事：创建唯一数据库连接、提供五个具名 Adapter、统一关闭。

```python
with LearningPersistence(db_path) as persistence:
    session = AssessmentSession(
        store=persistence.store,
        memory=persistence.memory,
        preferences=persistence.preferences,
        asked_questions=persistence.asked_questions,
        difficulty=persistence.difficulty,
        provider=provider,
        responder=responder,
    )
```

一次判决涉及的 SQLite Adapter 则通过公开协议声明自己的事务 owner：

```python
class TransactionParticipant(Protocol):
    @property
    def transaction_owner(self) -> LearningDatabase: ...
```

`LearningStateWriter` 会收集参与者的 `transaction_owner`。如果发现它们来自不同数据库，会在写入前
直接失败，而不是产生一半成功、一半失败的学习状态。

### 为什么没有把五个账本合成一个类

它们只是实现形状相似，修改原因并不相同：偏好、薄弱记忆、已问题目和难度各有自己的领域生命周期。
本轮只集中“连接所有权、装配与事务”这一项共同职责，保留各自的 Protocol、Dict Adapter 和 SQLite
Adapter。这样获得了生命周期 Locality，又没有为了目录整齐制造审美式合并。

## 4. AD-S3：把一张宽 Eval 表拆成三种严格 Case

### 原来的风险

旧 Eval case 同时容纳 ingest、assess 和 react 的所有字段，大部分字段都是可选值。这样的配置容易出现：

- `kind` 拼错后落入某个默认分支；
- assess case 写错 `provider` 或 `focus`，运行时才暴露；
- ingest case 意外携带 assess 字段，但没有人拒绝；
- 公共 runner 逐渐理解所有 case 的专属 setup。

最危险的结果不是报错，而是“跑了另一条 workflow 仍然显示绿色”。

### 严格的 per-kind 配置

现在 YAML 先经过带 discriminator 的 Pydantic union：

```python
_CaseEnvelope = Annotated[
    _IngestEnvelope | _AssessEnvelope | _ReactEnvelope,
    Field(discriminator="kind"),
]

class _StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
```

加载后得到三种窄 Case：

```python
Case = IngestCase | AssessCase | ReactCase
```

- `IngestCase` 只知道 source 与审批选择；
- `AssessCase` 只知道考核前置、作答、题型和 scope；
- `ReactCase` 只知道消息、cassette、fixture 与可选质量门。

三类 solver 分别处理自己的 setup，公共 runner 只消费统一的 `SolveResult`。未知 kind、未知枚举、
跨 kind 字段和缺失必填字段都会在 solve 前 fail closed。

这次还把 `SolveResult` 和共享 fixture 从大 harness 中提取出来，grader 不再反向 import harness，
删除了运行期 lazy import cycle。Eval harness 从约 1,588 行降到约 1,372 行。

## 5. AD-S4：让当前态文字与真实架构说同一种语言

代码结构健康，但文档描述过时，同样会制造技术债，尤其会误导后续维护者和 AI Agent。

本轮对齐了三组已落地事实：

- LearningResource 由稳定 locator 标识；内容变化形成新的 `ResourceRevision`，不是把 URL 叫作
  “内容寻址”。
- ADR-0005 已消解 `LearningTask`；全局 KB 不再按临时任务标题分区。
- Web Acquisition 已经支持真实 Fetch/Search，不再把它写成尚未交付。

修改范围只包括当前态 roadmap、生产 docstring 和用户可见 ingest 文案。历史 ADR、devrecords、
已完成 PRD 与 SQLite migrations 保留它们当时的时间语境，没有机械替换历史记录。

同时增加术语回归测试，扫描明确的当前态文件。它的用途不是禁止历史术语存在，而是防止生产说明重新漂回
已失效的架构。

## 6. 为什么这不是“多包了一层”

本轮使用删除性证据检查新 Module 是否真的隐藏复杂度：

```text
start_quiz_tool.py + CLI quiz.py + eval harness.py
新增 88 行，删除 327 行
```

三个新 Interface 也都有真实消费路径：

- `AssessmentSession` 同时服务 CLI quiz 与 ReAct `start_quiz`；
- `LearningPersistence` 同时拥有五类生产 SQLite Adapter；
- per-kind Eval Module 覆盖现有 ingest、assess、react 三类 case。

因此没有为单一实现提前制造 hypothetical seam，也没有按“文件看起来相似”重新分包。变化的中心是
调用者知识减少，而不是类和文件数量增加。

## 7. 明确保留的架构边界

本轮没有改变：

- ADR-0004：核心考核仍是确定性 workflow，LLM 只在出题和开放题判卷两个槽位工作；
- `AgentEvent` 事件脊柱，以及 trace、hook、CLI 投影和 eval replay 的关系；
- `kernel/` 不依赖 `domain/` 的分层守卫；
- SQLite schema、migration 与生产数据库；
- prompt、tool schema、Provider messages 与 cassette；
- Dict/SQLite 双 Adapter 和五类学习账本各自的领域职责。

## 8. 测试、提交与结果

四个切片分别提交，便于独立审查和回滚：

```text
b5790fe refactor: deepen assessment session
d3aa7d3 refactor: own learning persistence lifecycle
bf0efb4 refactor: deepen eval case modules
c6d04be docs: align current architecture language
364ec60 docs: record architecture deepening audit
```

最终门禁：

```text
ruff check .                pass
ruff format --check .       pass（175 files）
pyright                     pass（0 errors / 0 warnings）
lint-imports                pass（1 contract kept）
pytest                      841 passed
```

结果可以概括为：功能和外部契约没有扩张，但多轮状态、连接生命周期、事务身份、Eval 配置合法性和当前态
领域语言分别有了明确 owner。下一次增加考核会话能力、持久账本或 Eval case 时，需要同时理解和修改的地方
更少了。
