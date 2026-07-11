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
    SlidingWindowHistoryCompressor,
    TokenCounter,
)
from grandquiz.kernel.events import EventEmitter, EventSink
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
        index=index,
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
