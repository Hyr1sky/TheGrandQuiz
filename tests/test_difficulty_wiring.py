"""SE-S3 难度信号采集 + 记账接线 + 透明展示事件——断言外部可观测行为（台账末态 + 发出的事件）。

把 SE-S1 台账 + SE-S2 跨档规则接进 ``assess_once`` 编排后，验证**销账那一刻**：采集三路信号、
调 ``next_tier``、**仅真跨档**才写台账 + 发 ``DIFFICULTY_TIER_CHANGED``。全程用内存实现
（``DictDifficultyLedger`` / ``LearningMemory``）+ ``ManualClock`` + canned provider，避免真
LLM / cassette（销账走开放题的 LLM 判卷槽，verdict 由 fake 直接给）。

耗时信号（决策 B）经 ``ManualClock`` 的 ``tick`` 确定：开放题路径下 QUESTION_ASKED→ANSWER_JUDGED
之间恒隔 3 次 emit（判卷的一对 model span + ANSWER_JUDGED 自身），故 ``elapsed = 3 × tick ×
1000`` ms——``tick=1`` → 3000ms（快，≤FAST_MS）、``tick=100`` → 300000ms（慢，≥SLOW_MS）。

**SE-S5a 追加**（选择题选项数杠杆的 assess_once 接线）：难度档 → 目标选项数下传选择题出题请求。
用 ``_NumOptionsEchoProvider``（回读注入的选项数约束、回产对应数量选项）断言外部行为：高档 →
请求更多选项；``difficulty=None`` → 不注入、回落基线（默认路径等价改动前）。
"""

import json
import re
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console

from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.difficulty import (
    DEFAULT_TIER,
    DictDifficultyLedger,
    MasterySignals,
    tier_change_reason,
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.evals.harness import (
    MC_WRONG,
    QUOTES,
    AssessFakeProvider,
    build_stocked_store,
)
from grandquiz.interfaces.cli.composition import build_learning_stores
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Completion, Message, Role, Usage

_SEED = 42


def _harness(*, tick: float = 1.0) -> tuple[EventEmitter, list[AgentEvent]]:
    """确定性事件装配（可调 ``ManualClock`` tick 以控制答题耗时信号）：收集列表订阅同一 sink。"""
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(tick=tick), trace_id="run")
    return emitter, events


async def _discharge(
    *,
    history_verdicts: list[str],
    tick: float,
    difficulty: DictDifficultyLedger | None,
) -> tuple[str, list[AgentEvent]]:
    """把某 item 经 ``history_verdicts`` 喂到"观察中"（末位须为"对"），再答对一次触发销账。

    返回 (被考 item_id, 本轮销账 assess 的事件列表)。``history_verdicts`` 是销账**前**要建的
    verdict 历史——销账那刻被删记录的 ``verdict_history`` 即等于它（决策 A：无 +1）。
    """
    store, item_ids = build_stocked_store()
    memory = LearningMemory()
    target = item_ids[0]
    for verdict in history_verdicts:
        memory.record_verdict(target, verdict)  # type: ignore[arg-type]
    assert memory.state_of(target) == "观察中"  # 末位"对"应把它推到观察中

    emitter, events = _harness(tick=tick)
    result = await assess_once(
        store=store,
        provider=AssessFakeProvider(verdict="对"),  # 观察中 → 开放 → LLM 判卷返回"对" → 销账
        responder=ScriptedResponder(answer="任意"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        focus="weak",  # 锁定薄弱 / 观察中集，确保考的是 target
        difficulty=difficulty,
    )
    assert result.item_id == target
    assert result.question_type == "开放"  # 观察中 → 开放（走 LLM 判卷，与耗时 3-tick 假设一致）
    assert memory.state_of(target) is None  # 已销账
    return target, events


# --------------------------------------------------------------------------- #
# 销账 → 真跨档：升 / 降各一（决策 A 口径 + 决策 B 耗时 + 只真跨档才发事件）
# --------------------------------------------------------------------------- #


async def test_quick_clean_discharge_promotes_and_emits_event() -> None:
    # 快速清爽销账（错→对→对：被删 history=["错","对"] 长度 2 → 少轮 +1；无"勉强" +1；tick=1 → 快
    # +1 → 净分 +3 → 升一档）。断言台账升档 + 发 DIFFICULTY_TIER_CHANGED（from/to/reason 齐全）。
    difficulty = DictDifficultyLedger()
    target, events = await _discharge(
        history_verdicts=["错", "对"], tick=1.0, difficulty=difficulty
    )

    assert difficulty.tier_of(target) == DEFAULT_TIER + 1  # 3 → 4

    changed = [e for e in events if e.type == LearningEvent.DIFFICULTY_TIER_CHANGED]
    assert len(changed) == 1
    payload = changed[0].payload
    assert payload["item_id"] == target
    assert payload["concept"]  # 概念名非空
    assert payload["from_tier"] == DEFAULT_TIER
    assert payload["to_tier"] == DEFAULT_TIER + 1
    assert "上调难度" in payload["reason"]
    # 时序：DIFFICULTY_TIER_CHANGED 在 CONCEPT_STATE_CHANGED 之后、assessment.ended 之前。
    types = [e.type for e in events]
    assert (
        types.index(LearningEvent.CONCEPT_STATE_CHANGED)
        < types.index(LearningEvent.DIFFICULTY_TIER_CHANGED)
        < types.index("assessment.ended")
    )


async def test_dragged_struggle_discharge_demotes_and_emits_event() -> None:
    # 拖沓 + 勉强销账（错→勉强→错→对：被删 history 长度 4 → 拖轮 -1；掉过"勉强" -1；tick=100 → 慢
    # -1 → 净分 -3 → 降一档）。断言台账降档 + 发事件（降向 reason）。
    difficulty = DictDifficultyLedger()
    target, events = await _discharge(
        history_verdicts=["错", "勉强", "错", "对"], tick=100.0, difficulty=difficulty
    )

    assert difficulty.tier_of(target) == DEFAULT_TIER - 1  # 3 → 2

    changed = [e for e in events if e.type == LearningEvent.DIFFICULTY_TIER_CHANGED]
    assert len(changed) == 1
    payload = changed[0].payload
    assert payload["from_tier"] == DEFAULT_TIER
    assert payload["to_tier"] == DEFAULT_TIER - 1
    assert "下调难度" in payload["reason"]
    assert "掉过" in payload["reason"]  # 降档必因掉过"勉强"


# --------------------------------------------------------------------------- #
# 只真跨档才发：销账但净分维持（不跨档）→ 不发事件、台账不动
# --------------------------------------------------------------------------- #


async def test_discharge_but_net_maintains_does_not_emit_or_change() -> None:
    # 销账但净分维持（错→勉强→对：被删 history 长度 3 → 轮数中性 0；掉过"勉强" -1；tick=1 → 快 +1
    # → 净分 0 → 维持）。仅真跨档才发事件 → 不发 DIFFICULTY_TIER_CHANGED、台账仍是默认档。
    difficulty = DictDifficultyLedger()
    target, events = await _discharge(
        history_verdicts=["错", "勉强", "对"], tick=1.0, difficulty=difficulty
    )

    assert difficulty.tier_of(target) == DEFAULT_TIER  # 未变
    assert LearningEvent.DIFFICULTY_TIER_CHANGED not in {e.type for e in events}


# --------------------------------------------------------------------------- #
# 非销账轮：不动难度、不发事件（本期只在销账那一刻更新）
# --------------------------------------------------------------------------- #


async def test_non_discharge_wrong_answer_leaves_difficulty_untouched() -> None:
    # 答错入薄弱（非销账转移）→ 难度块整个不进（to_state != "销账"）：不发事件、台账不动。
    # SE-S5a 下此 item 停在默认档 3 → 不注入选项数约束（只在离开默认档后才落到题面），故仍用
    # 原 AssessFakeProvider（2 项 MC）即可，无需 num_options-aware provider。
    store, _item_ids = build_stocked_store()
    memory = LearningMemory()  # fresh → 选择题（MC）；选错 → 判错入薄弱
    difficulty = DictDifficultyLedger()
    emitter, events = _harness()

    result = await assess_once(
        store=store,
        provider=AssessFakeProvider(verdict="对"),  # MC 判卷走代码、verdict 对它无效
        responder=ScriptedResponder(answer=MC_WRONG),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        difficulty=difficulty,
    )

    assert result.verdict == "错"
    assert LearningEvent.DIFFICULTY_TIER_CHANGED not in {e.type for e in events}
    assert difficulty.tier_of(str(result.item_id)) == DEFAULT_TIER


async def test_non_discharge_correct_to_observing_leaves_difficulty_untouched() -> None:
    # 答对但未销账（薄弱 → 观察中）：非销账转移 → 难度不动、不发事件（销账信号只在真销账那刻可得）。
    store, item_ids = build_stocked_store()
    target = item_ids[0]
    memory = LearningMemory()
    memory.record_verdict(target, "错")  # → 薄弱
    difficulty = DictDifficultyLedger()
    emitter, events = _harness()

    result = await assess_once(
        store=store,
        provider=AssessFakeProvider(verdict="对"),  # 薄弱 → 追问 → 判对 → 观察中（不销账）
        responder=ScriptedResponder(answer="任意"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        focus="weak",
        difficulty=difficulty,
    )

    assert result.item_id == target
    assert result.concept_state == "观察中"  # 未销账
    assert LearningEvent.DIFFICULTY_TIER_CHANGED not in {e.type for e in events}
    assert difficulty.tier_of(target) == DEFAULT_TIER


# --------------------------------------------------------------------------- #
# difficulty=None 默认路径：销账也不发事件（字节等价探针）
# --------------------------------------------------------------------------- #


async def test_default_none_difficulty_emits_no_event_even_on_discharge() -> None:
    # 不传 difficulty（默认 None）：即便销账也**不**发 DIFFICULTY_TIER_CHANGED（难度块整个 gated）。
    _target, events = await _discharge(history_verdicts=["错", "对"], tick=1.0, difficulty=None)
    assert LearningEvent.DIFFICULTY_TIER_CHANGED not in {e.type for e in events}


# --------------------------------------------------------------------------- #
# tier_change_reason 纯函数单测（升 / 降各一）
# --------------------------------------------------------------------------- #


def test_tier_change_reason_promotion() -> None:
    reason = tier_change_reason(
        3, 4, MasterySignals(rounds_to_discharge=2, elapsed_ms=1000, had_struggle=False)
    )
    assert "上调难度" in reason
    assert "没掉过" in reason  # 升档必因全程未虚
    assert "2 轮就掌握" in reason  # 少轮佐证
    assert "答得快" in reason  # 快佐证


def test_tier_change_reason_demotion() -> None:
    reason = tier_change_reason(
        3, 2, MasterySignals(rounds_to_discharge=4, elapsed_ms=150_000, had_struggle=True)
    )
    assert "下调难度" in reason
    assert "掉过" in reason  # 降档必因掉过"勉强"
    assert "来回考了 4 轮" in reason  # 拖轮佐证
    assert "答得慢" in reason  # 慢佐证


# --------------------------------------------------------------------------- #
# 透传链 & 展示：build_learning_stores 5 元组 + printer 渲染
# --------------------------------------------------------------------------- #


def test_build_learning_stores_returns_five_persistent_pieces(tmp_path: Path) -> None:
    pieces = build_learning_stores(tmp_path / "learning.db")
    assert len(pieces) == 5  # store / memory / preference / asked_questions / difficulty
    store, memory, preferences, asked_questions, difficulty = pieces
    try:
        # 第五件是难度台账：新 item 读到默认档（跨会话留存的空态起点）。
        assert difficulty.tier_of("item-x") == DEFAULT_TIER
        difficulty.set_tier("item-x", 5)
        assert difficulty.tier_of("item-x") == 5
    finally:
        store.close()
        memory.close()
        preferences.close()
        asked_questions.close()
        difficulty.close()


def _event(event_type: str, payload: dict[str, object]) -> AgentEvent:
    return AgentEvent(type=event_type, seq=0, ts=0.0, trace_id="t", payload=payload)


def test_printer_renders_difficulty_change() -> None:
    console = Console(record=True, width=100)
    QuizEventPrinter(console)(
        _event(
            LearningEvent.DIFFICULTY_TIER_CHANGED,
            {"from_tier": 3, "to_tier": 4, "reason": "答得又快又干脆——上调难度"},
        )
    )
    out = console.export_text()
    assert "难度：3 → 4 档" in out
    assert "上调难度" in out


def test_printer_escapes_difficulty_reason_markup() -> None:
    # reason 理论上可含 markup 元字符（防御性 escape）：不抛 MarkupError、字面呈现即算过。
    console = Console(record=True, width=100)
    QuizEventPrinter(console)(
        _event(
            LearningEvent.DIFFICULTY_TIER_CHANGED,
            {"from_tier": 2, "to_tier": 1, "reason": "含 [bold]标记[/] 的 [/red"},
        )
    )
    assert "[bold]标记[/]" in console.export_text()


# --------------------------------------------------------------------------- #
# SE-S5a：选择题选项数杠杆的 assess_once 接线（高档 → 更多选项；difficulty=None → 基线）
# --------------------------------------------------------------------------- #


class _NumOptionsEchoProvider:
    """出题按注入的选项数约束回产对应数量的 MC 选项（验证 assess_once 把难度档 → 选项数注入出题）。

    从组装好的 messages 正则读出"恰好给出 N 个选项"的 N，回产 N 个平衡选项（正确项恒在下标 0）；
    无约束（``difficulty=None`` 默认路径）→ 回落 2 项。从 messages 回抽被考 item 真实证据使锚定门
    放行。``requested`` 只记 **enrich 出题**每次请求的选项数（不含 judge 调用），供外部断言"选项数随
    档位走"。SE-S5b 起高档（4/5）出题后会经 basic 干扰项 judge——此处 basic 槽一律回"合理干扰"
    （达标、不触发重生成），使"选项数随档位增"这条断言不被 judge 闸门干扰。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.roles: list[Role] = []
        self.requested: list[int] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        text = "\n".join(m.content for m in messages)
        if role != "enrich":
            # SE-S5b 干扰项 judge（basic 槽）：一律判"合理干扰"（达标），不触发重生成、
            # 不动 requested。
            verdict = json.dumps({"label": "合理干扰", "rationale": "测试理由"}, ensure_ascii=False)
            return Completion(text=verdict, usage=Usage(prompt_tokens=5, completion_tokens=2))
        quote = next(q for q in QUOTES if q in text)
        match = re.search(r"恰好给出 (\d+) 个选项", text)
        n = int(match.group(1)) if match else 2
        self.requested.append(n)
        options = ["正确选项", *(f"干扰项{i}" for i in range(1, n))]
        payload = {
            "question": "该知识点的核心是什么？",
            "options": options,
            "answer_index": 0,
            "cited_evidence": [quote],
        }
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


async def test_high_tier_requests_more_mc_options() -> None:
    # 全库所有 item 预置最高档（5）→ 无论选中哪个，target_option_count(5)=6 → 出题请求 6 个选项，
    # 且发出的 QUESTION_ASKED 带 6 个选项（外部可断言：选项数随档位增）。
    store, item_ids = build_stocked_store()
    difficulty = DictDifficultyLedger()
    for item_id in item_ids:
        difficulty.set_tier(item_id, 5)
    memory = LearningMemory()  # fresh → 选择题（MC）路径
    provider = _NumOptionsEchoProvider()
    emitter, events = _harness()

    result = await assess_once(
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="正确选项"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        difficulty=difficulty,
    )

    assert result.question_type == "选择题"
    assert provider.requested == [6]  # 档 5 → 6 个选项被请求（tier → num_options 接线生效）
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    assert len(asked.payload["options"]) == 6


async def test_default_none_difficulty_requests_baseline_options() -> None:
    # 对照：不传 difficulty（默认 None）→ 不读档、不注入选项数约束 → provider 回落 2 项。证明选项数
    # 注入 gated 在 difficulty is not None：默认路径不含"个选项"约束，出题请求等价改动前。
    store, _item_ids = build_stocked_store()
    memory = LearningMemory()  # fresh → 选择题（MC）路径
    provider = _NumOptionsEchoProvider()
    emitter, events = _harness()

    result = await assess_once(
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="正确选项"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    assert result.question_type == "选择题"
    assert provider.requested == [2]  # 无难度台账 → 无约束 → provider 回落基线 2
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    assert len(asked.payload["options"]) == 2


# --------------------------------------------------------------------------- #
# SE-S5b：选择题干扰项 judge 验收闸门的 assess_once 接线（高档触发 judge；默认档 / None 不触发）
# --------------------------------------------------------------------------- #


class _JudgingEchoProvider:
    """enrich 按注入选项数约束回产 MC；basic 评每个干扰项（返回可注入 ``DistractorLabel``）。

    MC 判卷走确定性代码、不打 basic 槽，故 assess_once 的 MC 路径下 basic **只可能**是 SE-S5b 的
    干扰项 judge——按 role 分流无歧义。``judge_calls`` 记 basic（评审）调用次数：断言高档（4/5）触发
    闸门（judge_calls > 0）、默认档 / difficulty=None 不触发（judge_calls == 0）。
    """

    def __init__(self, *, judge_label: str) -> None:
        self._judge_label = judge_label
        self.enrich_calls = 0
        self.judge_calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        text = "\n".join(m.content for m in messages)
        if role == "enrich":
            self.enrich_calls += 1
            quote = next(q for q in QUOTES if q in text)
            match = re.search(r"恰好给出 (\d+) 个选项", text)
            n = int(match.group(1)) if match else 2
            options = ["正确选项", *(f"干扰项{i}" for i in range(1, n))]
            payload = {
                "question": "该知识点的核心是什么？",
                "options": options,
                "answer_index": 0,
                "cited_evidence": [quote],
            }
            return Completion(
                text=json.dumps(payload, ensure_ascii=False),
                usage=Usage(prompt_tokens=7, completion_tokens=3),
            )
        self.judge_calls += 1
        verdict = json.dumps(
            {"label": self._judge_label, "rationale": "测试理由"}, ensure_ascii=False
        )
        return Completion(text=verdict, usage=Usage(prompt_tokens=5, completion_tokens=2))


async def test_high_tier_triggers_distractor_judge_gate() -> None:
    # 全库预置最高档（5）→ distractor_quality_floor(5)="合理干扰" 下传 → 出题后每个干扰项过 judge。
    # judge 全判"合理干扰"（达标）→ 首次即过。tier5 → 6 选项 → 5 个干扰项 → judge 被调 5 次（外部
    # 可断言：高档触发 judge 闸门）。
    store, item_ids = build_stocked_store()
    difficulty = DictDifficultyLedger()
    for item_id in item_ids:
        difficulty.set_tier(item_id, 5)
    memory = LearningMemory()  # fresh → 选择题（MC）路径
    provider = _JudgingEchoProvider(judge_label="合理干扰")
    emitter, _ = _harness()

    result = await assess_once(
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="正确选项"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        difficulty=difficulty,
    )

    assert result.question_type == "选择题"
    assert provider.enrich_calls == 1  # judge 全达标 → 无重生成
    assert provider.judge_calls == 5  # 6 选项 → 5 个干扰项各评一次（高档触发闸门）


async def test_default_tier_does_not_trigger_distractor_judge_gate() -> None:
    # 对照：全库预置默认档（3）→ distractor_quality_floor(3)=None → quality_floor 下传 None → judge
    # 一次都不调（judge_calls == 0）。默认档不加干扰项质量杠杆（与 SE-S5a 取向一致）。
    store, item_ids = build_stocked_store()
    difficulty = DictDifficultyLedger()
    for item_id in item_ids:
        difficulty.set_tier(item_id, DEFAULT_TIER)
    memory = LearningMemory()  # fresh → 选择题（MC）路径
    provider = _JudgingEchoProvider(judge_label="无效干扰")  # 若被调会不达标——但默认档不调
    emitter, _ = _harness()

    result = await assess_once(
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="正确选项"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        difficulty=difficulty,
    )

    assert result.question_type == "选择题"
    assert provider.judge_calls == 0  # 默认档 → 无 judge 闸门


async def test_none_difficulty_does_not_trigger_distractor_judge_gate() -> None:
    # 对照：不传 difficulty（默认 None）→ current_tier=None → quality_floor=None → judge 零调用。
    # 与 difficulty=None 的选项数 gated 一致：默认路径字节等价改动前，judge 一次都不碰。
    store, _item_ids = build_stocked_store()
    memory = LearningMemory()  # fresh → 选择题（MC）路径
    provider = _JudgingEchoProvider(judge_label="无效干扰")
    emitter, _ = _harness()

    result = await assess_once(
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="正确选项"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    assert result.question_type == "选择题"
    assert provider.judge_calls == 0  # difficulty=None → 无 judge 闸门
