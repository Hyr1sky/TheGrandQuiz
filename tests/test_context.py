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

from grandquiz.domain.learning.context import (
    learner_context_provider,
    render_learner_context,
)
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
    LearningTask,
)
from grandquiz.domain.learning.preference import (
    QUESTION_LANGUAGE_KEY,
    DictPreferenceMemory,
)
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.context import ContextBuilder, Partition
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


def _seed_item(store: LearningStore, task: LearningTask, concept: str, index: int) -> str:
    resource = LearningResource.create(task_id=task.task_id, url=f"file://local/{concept}.md")
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
    task = LearningTask.create("Py")
    store.add_task(task)
    closure_id = _seed_item(store, task, "闭包", 0)
    memory = LearningMemory()
    memory.record_verdict(closure_id, "错")  # → 薄弱

    text = render_learner_context(
        store=store, memory=memory, preferences=DictPreferenceMemory(), task=task
    )
    assert "闭包" in text
    assert "薄弱" in text


def test_render_includes_language_preference() -> None:
    store = LearningStore()
    task = LearningTask.create("Py")
    prefs = DictPreferenceMemory()
    prefs.set_preference(QUESTION_LANGUAGE_KEY, "英文")

    text = render_learner_context(
        store=store, memory=LearningMemory(), preferences=prefs, task=task
    )
    assert "英文" in text


def test_render_empty_when_no_weak_and_no_prefs() -> None:
    # 无薄弱点 + 无偏好 → 空串（ContextBuilder 据此跳过 memory 分区，不注入空块）。
    store = LearningStore()
    task = LearningTask.create("Py")
    text = render_learner_context(
        store=store, memory=LearningMemory(), preferences=DictPreferenceMemory(), task=task
    )
    assert text == ""


def test_render_weak_concepts_sorted_deterministically() -> None:
    # 多个薄弱概念按 item_id 升序渲染（确定性，不随 set 迭代序漂移 → replay 对得齐）。
    store = LearningStore()
    task = LearningTask.create("Py")
    store.add_task(task)
    memory = LearningMemory()
    ids = [_seed_item(store, task, c, i) for i, c in enumerate(["装饰器", "生成器", "闭包"])]
    for item_id in ids:
        memory.record_verdict(item_id, "错")

    text = render_learner_context(
        store=store, memory=memory, preferences=DictPreferenceMemory(), task=task
    )
    concept_by_id = dict(zip(ids, ["装饰器", "生成器", "闭包"], strict=True))
    ordered = [concept_by_id[i] for i in sorted(ids)]
    positions = [text.index(c) for c in ordered]
    assert positions == sorted(positions)


def test_provider_closure_reflects_memory_mutation() -> None:
    # 闭包捕获 memory/preferences 引用而非快照：考核推进后再 build，学情反映最新薄弱账。
    store = LearningStore()
    task = LearningTask.create("Py")
    store.add_task(task)
    item_id = _seed_item(store, task, "闭包", 0)
    memory = LearningMemory()
    prefs = DictPreferenceMemory()
    provider = learner_context_provider(store=store, memory=memory, preferences=prefs, task=task)

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
