"""R1-S3：ContextBuilder（M5）分区装配 + 学情记忆注入（薄弱 + 偏好）。

三层确定性核心走 TDD：
- kernel ``ContextBuilder``（领域无关机制）：有序分区 → system/memory/history/user 装配、str 与
  callable provider、空分区跳过、分区可增列表、预算 / 压缩策略接缝（本 issue 不实现、只留缝）。
- domain 学情 provider：把薄弱概念（Learning Memory）+ 偏好（Preference Memory）渲成紧凑"学情"
  文本；闭包每次现取（随考核推进刷新）；可扩展（加偏好 = 加渲染项）。
- ``Runner.run_agent_turn`` 经 ContextBuilder 装配 messages + 向后兼容（无 builder 退回原
  system + history）。
"""

from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.context import (
    learner_context_provider,
    render_learner_context,
)
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
)
from grandquiz.domain.learning.preference import (
    QUESTION_LANGUAGE_KEY,
    DictPreferenceMemory,
)
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.context import (
    BudgetCompressionPolicy,
    ContextBudgetExceeded,
    ContextBuilder,
    HeuristicTokenCounter,
    HistoryCompressor,
    Partition,
    PrunableHistoryCompressor,
    SlidingWindowHistoryCompressor,
    Summarizer,
    SummarizingHistoryCompressor,
    TokenCounter,
)
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.providers.base import Completion, Message, Role, ToolSpec, Usage

# --------------------------------------------------------------------------- #
# kernel ContextBuilder：分区装配（领域无关机制，只认名字 + 字符串 provider）
# --------------------------------------------------------------------------- #


def _build(partitions: Sequence[Partition], history: Sequence[Message], user: str) -> list[Message]:
    return ContextBuilder(partitions).build(history, user)


def test_build_assembles_system_then_memory_then_history_then_user() -> None:
    history = [
        Message(role="user", content="上一轮问"),
        Message(role="assistant", content="上一轮答"),
    ]
    messages = _build(
        [
            Partition(name="system", provider="系统提示"),
            Partition(name="memory", provider="学情块"),
        ],
        history,
        "本轮问题",
    )
    assert [(m.role, m.content) for m in messages] == [
        ("system", "系统提示"),
        ("system", "学情块"),
        ("user", "上一轮问"),
        ("assistant", "上一轮答"),
        ("user", "本轮问题"),
    ]


def test_callable_provider_reevaluated_each_build() -> None:
    # callable provider 每次 build 现取 → 学情随考核推进刷新（本 issue 兑现"记忆互通复用"的关键）。
    state = {"n": 0}

    def provider() -> str:
        state["n"] += 1
        return f"第{state['n']}次"

    builder = ContextBuilder([Partition(name="memory", provider=provider)])
    first = builder.build([], "q")
    second = builder.build([], "q")
    assert first[0].content == "第1次"
    assert second[0].content == "第2次"


def test_empty_content_partition_skipped() -> None:
    # 空内容分区（provider 返回空串）不塞进 messages——避免空 system 噪声。
    messages = _build(
        [Partition(name="system", provider="系统"), Partition(name="memory", provider="")],
        [],
        "q",
    )
    assert [(m.role, m.content) for m in messages] == [("system", "系统"), ("user", "q")]


def test_partitions_can_grow_order_preserved() -> None:
    # 扩展性：分区可增列表，日后加 persona / knowledge 零改机制——顺序即声明序。
    messages = _build(
        [
            Partition(name="system", provider="S"),
            Partition(name="persona", provider="P"),
            Partition(name="memory", provider="M"),
        ],
        [],
        "q",
    )
    assert [m.content for m in messages] == ["S", "P", "M", "q"]


def test_partition_carries_optional_budget_seam_without_effect() -> None:
    # 预算接缝：分区带可选 budget 字段（下一程 context compression 消费），本 issue 不裁剪。
    partition = Partition(name="memory", provider="很长的学情内容", budget=8)
    assert partition.budget == 8
    messages = _build([partition], [], "q")
    assert messages[0].content == "很长的学情内容"  # budget 现不生效（只留缝）


def test_compression_policy_hook_invoked_when_provided() -> None:
    # 压缩策略接缝：build 预留 policy 钩子。本 issue 不实现压缩；提供 policy 时按分区被调用，
    # 钉死接缝形状（下一程接真压缩器）。默认 policy=None 恒等透传。
    seen: list[tuple[str, str]] = []

    class _RecordingPolicy:
        def compress(self, partition: Partition, content: str) -> str:
            seen.append((partition.name, content))
            return content[:3]

    builder = ContextBuilder(
        [Partition(name="memory", provider="学情内容超长", budget=3)], policy=_RecordingPolicy()
    )
    messages = builder.build([], "q")
    assert seen == [("memory", "学情内容超长")]
    assert messages[0].content == "学情内容超"[:3]


# --------------------------------------------------------------------------- #
# domain 学情 provider：薄弱概念 + 偏好 → 紧凑"学情"文本（可扩展）
# --------------------------------------------------------------------------- #


def _seed_item(store: LearningStore, concept: str, index: int) -> str:
    resource = LearningResource.create(url=f"file://local/{concept}.md")
    store.add_resource(resource)
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept=concept,
        summary=f"{concept}的要点",
        evidence=[Evidence(quote=f"{concept}的原文")],
        confidence=0.9,
    )
    store.add_items([item])
    return item.item_id


def test_render_weak_concepts_by_name_and_state() -> None:
    store = LearningStore()
    closure_id = _seed_item(store, "闭包", 0)
    memory = LearningMemory()
    memory.record_verdict(closure_id, "错")  # → 薄弱

    text = render_learner_context(store=store, memory=memory, preferences=DictPreferenceMemory())
    assert "闭包" in text
    assert "薄弱" in text


def test_render_includes_language_preference() -> None:
    store = LearningStore()
    prefs = DictPreferenceMemory()
    prefs.set_preference(QUESTION_LANGUAGE_KEY, "英文")

    text = render_learner_context(store=store, memory=LearningMemory(), preferences=prefs)
    assert "英文" in text


def test_render_empty_when_no_weak_and_no_prefs() -> None:
    # 无薄弱点 + 无偏好 → 空串（ContextBuilder 据此跳过 memory 分区，不注入空块）。
    store = LearningStore()
    text = render_learner_context(
        store=store, memory=LearningMemory(), preferences=DictPreferenceMemory()
    )
    assert text == ""


def test_render_weak_concepts_sorted_deterministically() -> None:
    # 多个薄弱概念按 item_id 升序渲染（确定性，不随 set 迭代序漂移 → replay 对得齐）。
    store = LearningStore()
    memory = LearningMemory()
    ids = [_seed_item(store, c, i) for i, c in enumerate(["装饰器", "生成器", "闭包"])]
    for item_id in ids:
        memory.record_verdict(item_id, "错")

    text = render_learner_context(store=store, memory=memory, preferences=DictPreferenceMemory())
    concept_by_id = dict(zip(ids, ["装饰器", "生成器", "闭包"], strict=True))
    ordered = [concept_by_id[i] for i in sorted(ids)]
    positions = [text.index(c) for c in ordered]
    assert positions == sorted(positions)


def _seed_resource_with_topic(store: LearningStore, url: str, topic: str) -> str:
    resource = LearningResource.create(url=url).model_copy(update={"topic": topic})
    store.add_resource(resource)
    return resource.resource_id


def test_render_catalog_lists_resource_id_to_topic() -> None:
    # 目录注入：全库有 topic 的资源被渲成 {resource_id → topic} 库存清单，agent 不调工具即知库存。
    store = LearningStore()
    rid = _seed_resource_with_topic(store, "https://example.com/acp", "代理通信协议")
    text = render_learner_context(
        store=store, memory=LearningMemory(), preferences=DictPreferenceMemory()
    )
    assert "代理通信协议" in text
    assert rid in text  # 清单含 exact resource_id（供 LLM 填 start_quiz）


def test_render_catalog_absent_when_no_topic() -> None:
    # 空库 / 无 topic 资源 → 目录整段跳过（不注入空清单）；此处配无薄弱无偏好 → 整体空串。
    store = LearningStore()
    store.add_resource(LearningResource.create(url="https://example.com/plain"))  # topic=None
    text = render_learner_context(
        store=store, memory=LearningMemory(), preferences=DictPreferenceMemory()
    )
    assert text == ""


def test_render_catalog_sorted_by_resource_id_deterministic() -> None:
    # 多资源目录按 resource_id 升序（确定性，不随 dict 迭代序漂移 → replay 对齐）。
    store = LearningStore()
    rids = [
        _seed_resource_with_topic(store, u, t)
        for u, t in [
            ("https://example.com/z", "主题Z"),
            ("https://example.com/a", "主题A"),
            ("https://example.com/m", "主题M"),
        ]
    ]
    text = render_learner_context(
        store=store, memory=LearningMemory(), preferences=DictPreferenceMemory()
    )
    positions = [text.index(rid) for rid in sorted(rids)]
    assert positions == sorted(positions)  # 渲染顺序即 resource_id 升序


def test_provider_closure_reflects_memory_mutation() -> None:
    # 闭包捕获 memory/preferences 引用而非快照：考核推进后再 build，学情反映最新薄弱账。
    store = LearningStore()
    item_id = _seed_item(store, "闭包", 0)
    memory = LearningMemory()
    prefs = DictPreferenceMemory()
    provider = learner_context_provider(store=store, memory=memory, preferences=prefs)

    assert provider() == ""  # 初始无薄弱、无偏好
    memory.record_verdict(item_id, "错")  # 考核判错 → 薄弱账落
    assert "闭包" in provider()  # 再取即反映（callable 每次现取）


# --------------------------------------------------------------------------- #
# Runner.run_agent_turn：经 ContextBuilder 装配 + 向后兼容（无 builder 退回 system+history）
# --------------------------------------------------------------------------- #


class _CaptureProvider:
    """记录每次 complete 收到的 messages，恒返回 final 文本（无 tool_calls，单趟收敛）。"""

    def __init__(self) -> None:
        self.captured: list[list[Message]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.captured.append(list(messages))
        return Completion(text="回复", usage=Usage())


def _runner_emitter() -> EventEmitter:
    return EventEmitter(EventSink(), ManualClock(), trace_id="t")


async def test_run_agent_turn_uses_context_builder() -> None:
    provider = _CaptureProvider()
    builder = ContextBuilder(
        [Partition(name="system", provider="SYS"), Partition(name="memory", provider="学情")]
    )
    runner = Runner(provider=provider, emitter=_runner_emitter(), context_builder=builder)
    await runner.run_agent_turn("你好")
    assert [(m.role, m.content) for m in provider.captured[0]] == [
        ("system", "SYS"),
        ("system", "学情"),
        ("user", "你好"),
    ]


async def test_run_agent_turn_backward_compatible_without_builder() -> None:
    # 向后兼容：无 ContextBuilder 退回原 system + history 装配（既有 react 调用不破）。
    provider = _CaptureProvider()
    runner = Runner(provider=provider, emitter=_runner_emitter(), system_prompt="SYS")
    await runner.run_agent_turn("你好")
    assert [(m.role, m.content) for m in provider.captured[0]] == [
        ("system", "SYS"),
        ("user", "你好"),
    ]


async def test_context_builder_sees_accumulated_history() -> None:
    # 一个 Runner 贯穿多回合：build 收到的 history 是上一回合裁剪后的 user + final assistant。
    provider = _CaptureProvider()
    builder = ContextBuilder([Partition(name="system", provider="SYS")])
    runner = Runner(provider=provider, emitter=_runner_emitter(), context_builder=builder)
    await runner.run_agent_turn("第一问")
    await runner.run_agent_turn("第二问")
    assert [(m.role, m.content) for m in provider.captured[1]] == [
        ("system", "SYS"),
        ("user", "第一问"),
        ("assistant", "回复"),
        ("user", "第二问"),
    ]


# --------------------------------------------------------------------------- #
# C-wire 增量 2：Runner 接 prune()——排后台任务、下一轮开头收口、失败隔离、会话收尾兜底
# --------------------------------------------------------------------------- #


class _RaisingSummarizer:
    """确定性 fake：summarize 恒抛异常，验证 Runner 对 prune 失败的隔离（不炸turn）。"""

    async def summarize(self, prior_summary: str, messages: Sequence[Message]) -> str:
        raise RuntimeError("summarizer 炸了")


def _emitter_with_sink() -> tuple[EventEmitter, list[AgentEvent]]:
    sink = EventSink()
    collected: list[AgentEvent] = []
    sink.subscribe(collected.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), collected


async def test_run_agent_turn_prunes_with_post_commit_history_drained_next_turn() -> None:
    # prune() 用"本轮提交后"的 history 调用（含本轮 user+assistant）；且排的是后台任务——下一轮
    # 开头才落地（drain），这一轮的返回不等它。max_turns=1：第 2 轮结束即挤出第 1 轮。
    fake = _FakeSummarizer()
    compressor = SummarizingHistoryCompressor(fake, max_turns=1)
    builder = ContextBuilder(
        [Partition(name="system", provider="SYS")], history_compressor=compressor
    )
    provider = _CaptureProvider()
    runner = Runner(provider=provider, emitter=_runner_emitter(), context_builder=builder)

    await runner.run_agent_turn("第一问")  # 1 轮，未过窗口 → 排的任务是空操作
    assert fake.calls == []
    await runner.run_agent_turn("第二问")  # 2 轮 > 窗口 → 排它的折叠任务，但尚未落地
    assert fake.calls == []  # 还没到下一轮开头，任务没被 drain
    reply3 = await runner.run_agent_turn("第三问")  # 本轮开头 drain 上一轮的任务 → summarizer 收货
    assert reply3 == "回复"
    assert len(fake.calls) == 1
    assert fake.calls[0] == ("", ["第一问", "回复"])  # 折入的是被挤出的第 1 轮
    # 摘要落地后，第 3 轮送模型的 history 只剩第 2 轮原样 + 摘要 system 块（分区之后紧跟）。
    third_call_history = provider.captured[2][1:]  # [0]=SYS 分区，之后是摘要块 + history + user
    assert third_call_history[0].role == "system"
    assert "第一问+回复" in third_call_history[0].content


async def test_run_agent_turn_isolates_prune_failure_without_killing_later_turns() -> None:
    # 核心不变量（钉死 gap-review 的 blocking 发现）：上一轮排的 prune 任务在下一轮开头被 drain
    # 时若抛异常，必须被隔离（发 ERROR 事件、不冒泡）——不能把一个已经成功的 turn 拖成失败。
    compressor = SummarizingHistoryCompressor(_RaisingSummarizer(), max_turns=1)
    builder = ContextBuilder(
        [Partition(name="system", provider="SYS")], history_compressor=compressor
    )
    emitter, collected = _emitter_with_sink()
    runner = Runner(provider=_CaptureProvider(), emitter=emitter, context_builder=builder)

    await runner.run_agent_turn("第一问")  # 1 轮，未过窗口 → 排的任务是空操作，不会炸
    reply2 = await runner.run_agent_turn("第二问")  # 2 轮 > 窗口 → 排它的折叠任务（会炸，但还没跑）
    assert reply2 == "回复"  # 本轮自身正常返回——排的任务还没被 drain，不影响这一轮
    reply3 = await runner.run_agent_turn("第三问")  # 本轮开头 drain 上一轮的任务 → 它炸了，但被隔离
    assert reply3 == "回复"  # 隔离生效：第 3 轮照常拿到回复，没被上一轮的摘要失败拖累
    assert any(e.type == EventType.ERROR for e in collected)  # 失败仍可观测（进事件脊柱）


async def test_runner_aclose_drains_pending_prune_before_session_ends() -> None:
    # 会话收尾兜底：最后一轮排的任务若无"下一轮"来 drain，会被 asyncio.run 收尾时直接取消丢弃；
    # aclose() 是显式收口点，必须调用方（run_react 的 finally）负责调用。
    fake = _FakeSummarizer()
    compressor = SummarizingHistoryCompressor(fake, max_turns=1)
    builder = ContextBuilder(
        [Partition(name="system", provider="SYS")], history_compressor=compressor
    )
    runner = Runner(provider=_CaptureProvider(), emitter=_runner_emitter(), context_builder=builder)

    await runner.run_agent_turn("第一问")
    await runner.run_agent_turn("第二问")  # 排它的折叠任务——会话到此结束，没有"下一轮"
    assert fake.calls == []
    await runner.aclose()
    assert len(fake.calls) == 1  # 显式收口把它落地，没有被静默扔掉


# --------------------------------------------------------------------------- #
# C1：HeuristicTokenCounter——CJK 感知的确定性 token 估算（预算用途，非计费）
# --------------------------------------------------------------------------- #


def test_token_counter_empty_is_zero() -> None:
    assert HeuristicTokenCounter().count("") == 0


def test_token_counter_ascii_by_four_chars_per_token() -> None:
    # 拉丁/空白按 ~4 字符/token（GPT 系经验值）：ceil(len/4)。
    assert HeuristicTokenCounter().count("abcd") == 1  # ceil(4/4)
    assert HeuristicTokenCounter().count("hello world") == 3  # ceil(11/4)=3


def test_token_counter_cjk_by_one_token_per_char() -> None:
    # East-Asian Wide/Fullwidth（CJK/全角）按 ~1 token/字（密）。
    assert HeuristicTokenCounter().count("你好世界") == 4  # 4 wide → 4


def test_token_counter_mixed_sums_wide_and_narrow() -> None:
    # "你好 world"：wide=2（你好），narrow=6（空格+world）→ ceil(2/1 + 6/4)=ceil(3.5)=4。
    assert HeuristicTokenCounter().count("你好 world") == 4


def test_token_counter_is_deterministic_pure_function() -> None:
    # replay 命门：同串恒同值、无 clock/random。
    counter = HeuristicTokenCounter()
    text = "闭包 closure 是 what？"
    assert counter.count(text) == counter.count(text)


def test_token_counter_ratios_are_tunable() -> None:
    # 两个比率是构造参数：调 other_chars_per_token 影响估算（保确定性）。
    loose = HeuristicTokenCounter(other_chars_per_token=8.0)
    assert loose.count("abcdefgh") == 1  # ceil(8/8)=1（默认 4 会得 2）


def test_heuristic_counter_satisfies_token_counter_protocol() -> None:
    # 结构化契约：HeuristicTokenCounter 可当 TokenCounter 注入（pyright 静态校验此赋值）。
    counter: TokenCounter = HeuristicTokenCounter()
    assert counter.count("x") == 1


# --------------------------------------------------------------------------- #
# C2：BudgetCompressionPolicy（分区软预算·截断不抛）+ ContextBudgetExceeded（总硬上限·大声失败）
# --------------------------------------------------------------------------- #


def test_budget_policy_passthrough_when_no_budget() -> None:
    # 无 budget（默认 None）→ 原样透传（向后兼容：现有分区不受影响）。
    policy = BudgetCompressionPolicy(HeuristicTokenCounter())
    partition = Partition(name="m", provider="x")  # budget=None
    assert policy.compress(partition, "任意内容原样不动") == "任意内容原样不动"


def test_budget_policy_passthrough_when_within_budget() -> None:
    policy = BudgetCompressionPolicy(HeuristicTokenCounter())
    partition = Partition(name="m", provider="x", budget=100)
    assert policy.compress(partition, "短内容") == "短内容"


def test_budget_policy_truncates_over_budget_within_limit() -> None:
    # 超预算 → 确定性头截断 + 标记；不变量：截断后 token 数 <= budget。
    counter = HeuristicTokenCounter()
    policy = BudgetCompressionPolicy(counter)
    partition = Partition(name="m", provider="x", budget=5)
    content = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 ASCII → 9 tokens
    result = policy.compress(partition, content)
    assert result != content
    assert counter.count(result) <= 5
    assert result.endswith("…")  # 缀截断标记（budget 够放标记时）
    assert content.startswith(result[:-1])  # 保的是开头前缀


def test_budget_policy_tiny_budget_below_marker_no_crash() -> None:
    # 预算连标记都放不下 → best-effort 硬截，仍 <= budget、仍不抛（软预算永不抛）。
    counter = HeuristicTokenCounter()
    policy = BudgetCompressionPolicy(counter)
    partition = Partition(name="m", provider="x", budget=1)
    result = policy.compress(partition, "abcdefghijklmnop")  # 4 tokens
    assert counter.count(result) <= 1


def test_budget_policy_deterministic() -> None:
    counter = HeuristicTokenCounter()
    policy = BudgetCompressionPolicy(counter)
    partition = Partition(name="m", provider="x", budget=3)
    content = "重复内容很多很多很多很多很多很多很多很多"
    assert policy.compress(partition, content) == policy.compress(partition, content)


def test_context_builder_applies_budget_policy_to_partition() -> None:
    # policy 经 build 的 _resolve 钩子作用到分区内容：超预算分区被裁进预算。
    counter = HeuristicTokenCounter()
    builder = ContextBuilder(
        [Partition(name="m", provider="很长很长的学情内容" * 10, budget=5)],
        policy=BudgetCompressionPolicy(counter),
    )
    messages = builder.build([], "q")
    assert counter.count(messages[0].content) <= 5


def test_context_builder_no_ceiling_never_raises() -> None:
    # 向后兼容：total_budget None（默认）→ 从不查、从不抛（现有 run_react / 测试 / cassette 不破）。
    builder = ContextBuilder([Partition(name="system", provider="x" * 10000)])
    messages = builder.build([], "q")
    assert messages[-1].content == "q"


def test_context_builder_within_ceiling_ok() -> None:
    counter = HeuristicTokenCounter()
    builder = ContextBuilder(
        [Partition(name="system", provider="短提示")], counter=counter, total_budget=1000
    )
    assert builder.build([], "q")[-1].content == "q"


def test_context_builder_budget_policy_truncation_is_what_avoids_ceiling_breach() -> None:
    # 因果性（非各测各的）：同样的分区内容 + 同样的 total_budget，无 policy 时越硬上限抛异常；
    # 加上 BudgetCompressionPolicy 后分区被头截断、总量落回上限内，不再抛——证明是 policy 起效，
    # 不是巧合/取整误差。
    counter = HeuristicTokenCounter()
    partitions = [Partition(name="m", provider="内容" * 200, budget=5)]
    with pytest.raises(ContextBudgetExceeded):
        ContextBuilder(partitions, counter=counter, total_budget=20).build([], "q")
    messages = ContextBuilder(
        partitions, policy=BudgetCompressionPolicy(counter), counter=counter, total_budget=20
    ).build([], "q")
    assert messages[-1].content == "q"


def test_context_builder_budget_policy_only_bounds_partitions_not_history_or_user() -> None:
    # policy 只裁分区内容：history / user_message 撑爆总预算时，即便分区被截得再小也救不回来——
    # 硬上限的"大声失败"不能被 policy 悄悄绕过。
    counter = HeuristicTokenCounter()
    huge_history = [Message(role="user", content="超长历史" * 200)]
    with pytest.raises(ContextBudgetExceeded):
        ContextBuilder(
            [Partition(name="m", provider="短", budget=5)],
            policy=BudgetCompressionPolicy(counter),
            counter=counter,
            total_budget=20,
        ).build(huge_history, "q")


def test_context_builder_raises_when_total_over_ceiling() -> None:
    # 总硬上限：装配后总 token 超上限 → 大声失败（ContextBudgetExceeded），不静默截断成残缺上下文。
    counter = HeuristicTokenCounter()
    builder = ContextBuilder(
        [Partition(name="system", provider="这是一段很长的系统提示内容" * 20)],
        counter=counter,
        total_budget=5,
    )
    with pytest.raises(ContextBudgetExceeded) as exc_info:
        builder.build([], "问题")
    assert exc_info.value.ceiling == 5
    assert exc_info.value.used > 5


# --------------------------------------------------------------------------- #
# C3a：SlidingWindowHistoryCompressor（保最近 N 轮原样、老轮丢弃；确定性，无 LLM）
# --------------------------------------------------------------------------- #


def _turns(n: int) -> list[Message]:
    # 造 n 轮 [user_i, assistant_i]——run_agent_turn 裁剪后 history 的形状（u/a 交替）。
    messages: list[Message] = []
    for i in range(n):
        messages.append(Message(role="user", content=f"问{i}"))
        messages.append(Message(role="assistant", content=f"答{i}"))
    return messages


def test_sliding_window_keeps_all_when_within_window() -> None:
    compressor = SlidingWindowHistoryCompressor(max_turns=3)
    history = _turns(2)  # 4 条 <= 窗口 6 条
    assert compressor.compress(history) == history


def test_sliding_window_keeps_last_n_turns() -> None:
    # 超窗口 → 只保最近 max_turns 轮（= max_turns*2 条），更早的丢。
    compressor = SlidingWindowHistoryCompressor(max_turns=2)
    result = compressor.compress(_turns(4))  # 8 条 → 保最后 4 条
    assert [m.content for m in result] == ["问2", "答2", "问3", "答3"]


def test_sliding_window_zero_turns_drops_all() -> None:
    # max_turns=0 → 全丢（不能被 history[-0:] 的"取全部"陷阱坑到）。
    assert SlidingWindowHistoryCompressor(max_turns=0).compress(_turns(3)) == []


def test_sliding_window_deterministic() -> None:
    compressor = SlidingWindowHistoryCompressor(max_turns=2)
    history = _turns(5)
    assert compressor.compress(history) == compressor.compress(history)


def test_sliding_window_satisfies_history_compressor_protocol() -> None:
    compressor: HistoryCompressor = SlidingWindowHistoryCompressor()
    assert compressor.compress([]) == []


def test_context_builder_applies_history_compressor_before_assembly() -> None:
    # build 在 extend history 前调 compressor：只有最近 1 轮 + system + 当前 user 进 messages。
    builder = ContextBuilder(
        [Partition(name="system", provider="S")],
        history_compressor=SlidingWindowHistoryCompressor(max_turns=1),
    )
    messages = builder.build(_turns(3), "现在")
    assert [(m.role, m.content) for m in messages] == [
        ("system", "S"),
        ("user", "问2"),
        ("assistant", "答2"),
        ("user", "现在"),
    ]


def test_context_builder_no_history_compressor_keeps_all() -> None:
    # 向后兼容：无 compressor（默认 None）→ history 原样全展（现有 run_react / 测试不破）。
    builder = ContextBuilder([Partition(name="system", provider="S")])
    messages = builder.build(_turns(2), "现在")
    assert [m.content for m in messages] == ["S", "问0", "答0", "问1", "答1", "现在"]


# --------------------------------------------------------------------------- #
# C3b：SummarizingHistoryCompressor（滚动摘要 + 最近窗口；sync compress 读 / async prune 写）
# --------------------------------------------------------------------------- #


class _FakeSummarizer:
    """确定性 fake：把折入的消息条数追加进 prior_summary（无 LLM，供 TDD 钉逻辑）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def summarize(self, prior_summary: str, messages: Sequence[Message]) -> str:
        self.calls.append((prior_summary, [m.content for m in messages]))
        folded = "+".join(m.content for m in messages)  # 确定性：折入内容拼接，便于断言
        return f"{prior_summary}>{folded}" if prior_summary else folded


def test_summarizing_satisfies_protocols() -> None:
    _c: HistoryCompressor = SummarizingHistoryCompressor(_FakeSummarizer())
    _s: Summarizer = _FakeSummarizer()
    assert _c.compress([]) == []


async def test_summarizing_within_window_no_prune_no_summary() -> None:
    # 未超窗口 → prune 不摘要、compress 原样返回、无摘要消息、summarizer 零调用。
    fake = _FakeSummarizer()
    compressor = SummarizingHistoryCompressor(fake, max_turns=3)
    history = _turns(2)
    await compressor.prune(history)
    assert fake.calls == []
    assert compressor.compress(history) == history


async def test_summarizing_prune_folds_evicted_and_compress_prepends_summary() -> None:
    # 超窗口 → prune 把被挤出的老轮折进滚动摘要；compress 返回 [system(摘要)] + 最近窗口。
    fake = _FakeSummarizer()
    compressor = SummarizingHistoryCompressor(fake, max_turns=2)
    history = _turns(4)  # 4 轮，窗口 2 → 轮0、轮1 被挤出（2 轮 = 4 条）
    await compressor.prune(history)
    assert len(fake.calls) == 1
    assert fake.calls[0] == ("", ["问0", "答0", "问1", "答1"])  # 折入前 2 轮
    result = compressor.compress(history)
    assert result[0].role == "system"
    assert "问0+答0+问1+答1" in result[0].content  # 被挤出的前 2 轮进了摘要
    assert [m.content for m in result[1:]] == ["问2", "答2", "问3", "答3"]  # 最近 2 轮原样


async def test_summarizing_prune_is_incremental_across_turns() -> None:
    # 增量：第二次 prune 只折"新被挤出"的那轮，且带上上次的滚动摘要（LangChain summary-buffer）。
    fake = _FakeSummarizer()
    compressor = SummarizingHistoryCompressor(fake, max_turns=2)
    await compressor.prune(_turns(3))  # 轮0 被挤出
    await compressor.prune(_turns(4))  # 新增轮1 被挤出
    assert len(fake.calls) == 2
    assert fake.calls[0] == ("", ["问0", "答0"])
    assert fake.calls[1][0] == "问0+答0"  # 第二次带上一次的滚动摘要作 prior
    assert fake.calls[1][1] == ["问1", "答1"]


async def test_summarizing_prune_idempotent_when_no_new_eviction() -> None:
    # 无新老轮被挤出 → prune 不再调 summarizer（幂等，别重复摘同一轮）。
    fake = _FakeSummarizer()
    compressor = SummarizingHistoryCompressor(fake, max_turns=2)
    await compressor.prune(_turns(3))
    await compressor.prune(_turns(3))
    assert len(fake.calls) == 1


# --------------------------------------------------------------------------- #
# C-wire 增量 2：PrunableHistoryCompressor 协议 + ContextBuilder.prune()（能力探测委托）
# --------------------------------------------------------------------------- #


class _SyncPruneCompressor:
    """满足 HistoryCompressor，但带一个同名同步（非 async）``prune``——钉死"runtime_checkable
    不验证 async 性"这个已知陷阱：isinstance 会放行，await 其返回值才在别处炸出费解的 TypeError。
    """

    def compress(self, history: Sequence[Message]) -> list[Message]:
        return list(history)

    def prune(self, history: Sequence[Message]) -> None:  # 故意不是 async def
        return None


def test_prunable_history_compressor_protocol_matches_only_summarizing() -> None:
    # 能力探测边界：SummarizingHistoryCompressor 满足（真有 prune）；SlidingWindow 不满足（无状态、
    # 没有可折叠的东西，不该被强迫实现这个协议）。
    assert isinstance(SummarizingHistoryCompressor(_FakeSummarizer()), PrunableHistoryCompressor)
    assert not isinstance(SlidingWindowHistoryCompressor(), PrunableHistoryCompressor)


async def test_context_builder_prune_noop_without_history_compressor() -> None:
    # 向后兼容：无 history_compressor（默认 None）→ 静默跳过，不炸（现有装配不受影响）。
    builder = ContextBuilder([Partition(name="system", provider="S")])
    await builder.prune(_turns(3))


async def test_context_builder_prune_noop_with_non_prunable_compressor() -> None:
    # SlidingWindowHistoryCompressor 满足 HistoryCompressor 但不满足 PrunableHistoryCompressor
    # （没有 prune 方法）→ ContextBuilder.prune 能力探测后静默跳过，不因"没有 prune"报错。
    builder = ContextBuilder(
        [Partition(name="system", provider="S")],
        history_compressor=SlidingWindowHistoryCompressor(max_turns=2),
    )
    await builder.prune(_turns(3))


async def test_context_builder_prune_delegates_to_summarizing_compressor() -> None:
    # 委托：真有 prune 能力的压缩器被调用，等价于直接调 compressor.prune(history)。
    fake = _FakeSummarizer()
    compressor = SummarizingHistoryCompressor(fake, max_turns=1)
    builder = ContextBuilder(
        [Partition(name="system", provider="S")], history_compressor=compressor
    )
    await builder.prune(_turns(2))
    assert len(fake.calls) == 1


async def test_context_builder_prune_rejects_sync_prune_implementation() -> None:
    # 防护网：结构上满足 PrunableHistoryCompressor（isinstance 通过）但 prune 非 async 的实现，
    # 须在这里就报出指名道姓的 AssertionError，而不是让调用方在 await 处收到费解的 TypeError。
    builder = ContextBuilder(
        [Partition(name="system", provider="S")],
        history_compressor=_SyncPruneCompressor(),
    )
    with pytest.raises(AssertionError, match="async def"):
        await builder.prune([])
