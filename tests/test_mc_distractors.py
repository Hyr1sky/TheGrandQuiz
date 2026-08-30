"""选择题干扰项反-tell 门测试（缝 3）——注入假 provider，无真实 LLM。

被测：``_parse_mc`` 新增的两道**便宜的确定性反-tell 门**（只挡表面泄漏、不测 plausibility，
真打分是 Tier 2 LLM-judge，不在本 issue）：

- **meta 选项禁令**：以指代前缀起头（以上/上述/综上）或含 all/none of the above → ModelRetry；
  持续返回则有界重试用尽 → QuestionError（provider 被多调，证明发生重试）。
- **长度离群**：正确项 > 最长干扰项的 2 倍（答案被长度出卖，只查"独长"一向）→ ModelRetry。

并断言这两道门**放行既有假 provider 的平衡 MC 选项**（["正确选项","干扰项"]、中 / 英文平衡选项），
不误伤——阈值保守、只抓 egregious 泄漏。
"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.assessment.question import (
    MultipleChoiceQuestion,
    QuestionError,
    generate_multiple_choice,
)
from grandquiz.domain.learning.difficulty import DistractorQualityPolicy
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, Usage

_QUOTE = "闭包捕获的是变量而非值"


class _FixedProvider:
    """返回固定文本、计被调次数（同 test_question 的假 provider 模式，本文件自包含地复制）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        return Completion(text=self.text, usage=Usage(prompt_tokens=5, completion_tokens=2))


class _ExplodingProvider:
    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        del messages, role, tools
        raise RuntimeError("provider unavailable")


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


def _item() -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id="res",
        concept="闭包",
        summary="函数捕获定义时的作用域",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )


def _mc_json(*, options: list[str], answer_index: int) -> str:
    payload: dict[str, object] = {
        "question": "闭包捕获的是什么？",
        "options": options,
        "answer_index": answer_index,
        "cited_evidence": [_QUOTE],
    }
    return json.dumps(payload, ensure_ascii=False)


# --- meta 选项门 -----------------------------------------------------------------------


async def test_meta_option_retries_then_raises() -> None:
    # meta 选项（"以上都对"）泄漏题型、非真干扰项 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(
        _mc_json(options=["变量本身", "值的快照", "以上都对"], answer_index=0)
    )
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2  # 每次都命中 meta 门 → 重试用尽


async def test_meta_option_english_retries_then_raises() -> None:
    # 英文 meta 选项（"none of the above"，大小写不敏感）同样被挡。
    provider = _FixedProvider(
        _mc_json(
            options=["the variable itself", "a value snapshot", "None Of The Above"],
            answer_index=0,
        )
    )
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_balanced_mc_without_meta_passes() -> None:
    # 无 meta 选项、长度平衡 → 首次即过、无重试。
    provider = _FixedProvider(_mc_json(options=["变量本身", "值的快照"], answer_index=0))
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert isinstance(mc, MultipleChoiceQuestion)
    assert provider.calls == 1


async def test_provider_failure_closes_generation_span_without_invalid_output_diagnosis() -> None:
    emitter, events = _emitter()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await generate_multiple_choice(
            _item(), provider=_ExplodingProvider(), emitter=emitter, parent_span_id="a"
        )

    ended = next(
        event for event in events if event.type == "learning.multiple_choice_generation.ended"
    )
    assert ended.payload["ok"] is False
    assert ended.payload["stage"] == "model_call"
    assert ended.payload["error_type"] == "RuntimeError"
    assert "reason_code" not in ended.payload


@pytest.mark.parametrize(
    "meta_option",
    ["以上都对", "以上都不对", "以上皆对", "以上皆错", "all of the above", "None Of The Above"],
)
async def test_all_meta_phrases_are_blocked(meta_option: str) -> None:
    # 逐个钉住每条 meta 形态都被挡（防"某条短语被误删仍全绿"的 mutation）。
    provider = _FixedProvider(
        _mc_json(options=["变量本身", "值的快照", meta_option], answer_index=0)
    )
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


@pytest.mark.parametrize(
    "options",
    [
        ["两者都对齐", "其一偏移", "另一偏移"],  # 含"都对"子串但非 meta
        ["指针都不对齐边界", "指针对齐", "地址未定义"],  # 含"都不对"子串但非 meta
    ],
)
async def test_legit_options_with_meta_substrings_pass(options: list[str]) -> None:
    # 合法选项含 "都对"/"都不对" 子串但不以指代前缀起头 → 不触发 meta 门（回归 MEDIUM）。
    provider = _FixedProvider(_mc_json(options=options, answer_index=0))
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert list(mc.options) == options
    assert provider.calls == 1  # 首次即过、无误伤重试


# --- 长度离群门 ------------------------------------------------------------------------


async def test_correct_option_much_longer_retries_then_raises() -> None:
    # 正确项 > 最长干扰项的 2 倍（答案被长度出卖）→ ModelRetry 用尽 → QuestionError。
    long_correct = "变量本身而不是它在闭包定义那一刻被拷贝下来的一个静态的值的快照副本"
    provider = _FixedProvider(_mc_json(options=[long_correct, "值", "快照"], answer_index=0))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_short_correct_with_longer_distractors_passes() -> None:
    # 正解是单一术语、干扰项是完整错误短语——合法形态，不再被"独短"误判（回归：LOW 修复，只查独长）。
    provider = _FixedProvider(
        _mc_json(options=["变量", "外层的作用域链", "闭包捕获环境"], answer_index=0)
    )
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert isinstance(mc, MultipleChoiceQuestion)
    assert provider.calls == 1


async def test_length_outlier_just_over_2x_triggers() -> None:
    # 正确项 7 字、最长干扰项 3 字：7 > 2×3=6 → 触发（钉住 2× 下界，防"放宽到 5×"的 mutation）。
    provider = _FixedProvider(
        _mc_json(options=["一二三四五六七", "甲乙丙", "丁戊己"], answer_index=0)
    )
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_length_at_2x_boundary_passes() -> None:
    # 正确项 6 字、最长干扰项 3 字：6 > 6 为假 → 恰好放行（钉住阈值是"严格大于 2×"）。
    provider = _FixedProvider(
        _mc_json(options=["一二三四五六", "甲乙丙", "丁戊己"], answer_index=0)
    )
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert isinstance(mc, MultipleChoiceQuestion)
    assert provider.calls == 1


async def test_length_balanced_mc_passes() -> None:
    # 长度平行的三选项 → 首次即过。
    provider = _FixedProvider(
        _mc_json(options=["变量本身", "值的快照", "外层作用域"], answer_index=0)
    )
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert isinstance(mc, MultipleChoiceQuestion)
    assert provider.calls == 1


# --- 不误伤既有假 provider 的平衡选项 --------------------------------------------------


@pytest.mark.parametrize(
    ("options", "answer_index"),
    [
        (["正确选项", "干扰项"], 0),  # test_assessment case 3/4 的假选项
        (["值的快照", "变量本身"], 1),  # test_question / test_question_language 中文选项
        (["a value snapshot", "the variable itself"], 1),  # test_question_language 英文选项
    ],
)
async def test_existing_fake_options_pass_anti_tell_gates(
    options: list[str], answer_index: int
) -> None:
    # 反-tell 门必须放行既有测试用的平衡假选项（阈值过激应放宽门，绝不改被 forbidden 的测试）。
    provider = _FixedProvider(_mc_json(options=options, answer_index=answer_index))
    emitter, events = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert list(mc.options) == options
    assert provider.calls == 1  # 首次即过、无误伤重试
    assert [e.type for e in events] == [
        "learning.multiple_choice_generation.started",
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        "learning.multiple_choice_generation.ended",
    ]


# --- SE-S5a：选项数硬杠杆门（num_options 传入才生效；None 时行为逐字节等价改动前）------------

_SIX_OPTIONS = ["变量本身", "值的快照", "外层作用域", "定义时环境", "调用时环境", "全局对象"]
_THREE_OPTIONS = ["变量本身", "值的快照", "外层作用域"]


class _MessageCapturingProvider:
    """返回固定文本、并留存最后一次收到的 messages（用于断言选项数约束被注入）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.last_text = ""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.last_text = "\n".join(m.content for m in messages)
        return Completion(text=self.text, usage=Usage(prompt_tokens=5, completion_tokens=2))


async def test_num_options_six_with_six_options_passes() -> None:
    # 传 num_options=6、provider 恰返回 6 个平衡选项 → 首次即过，options 数 == 6，且注入了约束。
    provider = _MessageCapturingProvider(_mc_json(options=_SIX_OPTIONS, answer_index=0))
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a", num_options=6
    )
    assert len(mc.options) == 6
    assert provider.calls == 1
    assert "6 个选项" in provider.last_text  # 选项数约束确实注入了出题请求


async def test_num_options_six_with_three_options_retries_then_raises() -> None:
    # 传 num_options=6、provider 只回 3 个选项 → 选项数门不达标 → ModelRetry 用尽 → QuestionError。
    provider = _MessageCapturingProvider(_mc_json(options=_THREE_OPTIONS, answer_index=0))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(),
            provider=provider,
            emitter=emitter,
            parent_span_id="a",
            max_attempts=2,
            num_options=6,
        )
    assert provider.calls == 2  # 每次都因选项不足重试 → 用尽


async def test_num_options_is_an_exact_contract_and_rejects_extra_options() -> None:
    provider = _MessageCapturingProvider(
        _mc_json(options=[*_THREE_OPTIONS, "定义时环境", "调用时环境"], answer_index=0)
    )
    emitter, events = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(),
            provider=provider,
            emitter=emitter,
            parent_span_id="a",
            max_attempts=2,
            num_options=4,
        )

    rejected = [
        event
        for event in events
        if event.type == "learning.multiple_choice_generation.attempt_rejected"
    ]
    assert [event.payload["reason_code"] for event in rejected] == [
        "option_count_unmet",
        "option_count_unmet",
    ]


async def test_num_options_none_with_three_options_still_passes() -> None:
    # **关键对照**：不传 num_options（默认 None）→ 选项数门整个不参与，3 选项仍过既有 >= 2 门。
    # 证明默认路径逐字节等价改动前：既有调用方 / eval harness 不受新门影响。
    provider = _MessageCapturingProvider(_mc_json(options=_THREE_OPTIONS, answer_index=0))
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert len(mc.options) == 3
    assert provider.calls == 1  # 首次即过、无重试
    assert "个选项" not in provider.last_text  # None 时不注入任何选项数约束


async def test_correct_answer_claims_must_be_directly_supported_by_evidence() -> None:
    provider = _MessageCapturingProvider(_mc_json(options=_THREE_OPTIONS, answer_index=0))
    emitter, _ = _emitter()

    await generate_multiple_choice(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        num_options=3,
    )

    assert "摘要只用于帮助理解概念" in provider.last_text
    assert "正确答案的实质主张必须由 cited_evidence 直接支持" in provider.last_text


# --- SE-S5b：集合质量策略（传入才生效；None 时 judge 零调用）----


class _JudgingProvider:
    """按 role 分流：``enrich`` 出固定 MC、``basic`` 评干扰项（返回可注入的 ``DistractorLabel``）。

    judge_distractor 走 role=basic（同判卷官），MC 出题走 role=enrich——本文件 generate_multiple_
    choice 路径下 basic 槽**只可能**是 judge（MC 判卷是确定性代码、不打 LLM），按 role 分流无歧义。
    分别计 ``enrich_calls``（出题）与 ``judge_calls``（评审）。
    """

    def __init__(self, *, mc_json: str, judge_label: str) -> None:
        self._mc_json = mc_json
        self._judge_label = judge_label
        self.enrich_calls = 0
        self.judge_calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        if role == "enrich":
            self.enrich_calls += 1
            return Completion(text=self._mc_json, usage=Usage(prompt_tokens=5, completion_tokens=2))
        self.judge_calls += 1
        verdict = json.dumps(
            {"label": self._judge_label, "rationale": "测试用固定理由"}, ensure_ascii=False
        )
        return Completion(text=verdict, usage=Usage(prompt_tokens=5, completion_tokens=2))


async def test_quality_policy_invalid_distractors_exhaust_bounded_repairs() -> None:
    provider = _JudgingProvider(
        mc_json=_mc_json(options=["变量本身", "值的快照", "外层作用域"], answer_index=0),
        judge_label="无效干扰",
    )
    emitter, events = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(),
            provider=provider,
            emitter=emitter,
            parent_span_id="a",
            max_attempts=2,
            quality_policy=DistractorQualityPolicy(minimum_label="较弱干扰"),
        )
    assert provider.enrich_calls == 2
    # 第二版复用了完全相同的选项；一次生成任务内不会重复 judge 同一文本。
    assert provider.judge_calls == 2
    rejected = [
        event
        for event in events
        if event.type == "learning.multiple_choice_generation.attempt_rejected"
    ]
    assert [event.payload["stage"] for event in rejected] == ["generation", "repair"]
    assert [event.payload["retained_distractor_count"] for event in rejected] == [0, 0]


async def test_quality_policy_reasonable_distractors_pass_first_try() -> None:
    provider = _JudgingProvider(
        mc_json=_mc_json(options=["变量本身", "值的快照", "外层作用域"], answer_index=0),
        judge_label="合理干扰",
    )
    emitter, events = _emitter()

    mc = await generate_multiple_choice(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        quality_policy=DistractorQualityPolicy(minimum_label="较弱干扰", minimum_reasonable=2),
    )
    assert isinstance(mc, MultipleChoiceQuestion)
    assert provider.enrich_calls == 1  # 首次即过
    assert provider.judge_calls == 2  # 2 个干扰项各评一次
    generation = next(
        event for event in events if event.type == "learning.multiple_choice_generation.started"
    )
    basic_starts = [
        e for e in events if e.type == EventType.MODEL_STARTED and e.payload.get("role") == "basic"
    ]
    assert len(basic_starts) == 2
    assert all(e.parent_span_id == generation.span_id for e in basic_starts)


async def test_quality_policy_none_never_calls_judge() -> None:
    provider = _JudgingProvider(
        mc_json=_mc_json(options=["变量本身", "值的快照", "外层作用域"], answer_index=0),
        judge_label="无效干扰",  # 若 judge 被调会判不达标——但 None 时根本不调
    )
    emitter, events = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert isinstance(mc, MultipleChoiceQuestion)
    assert provider.enrich_calls == 1  # 首次即过
    assert provider.judge_calls == 0  # **judge 零调用**——默认路径不触发闸门
    assert [e.type for e in events] == [
        "learning.multiple_choice_generation.started",
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        "learning.multiple_choice_generation.ended",
    ]
    assert all(e.payload.get("role") != "basic" for e in events)  # 无 judge（basic）span


class _RepairingDistractorProvider:
    """首版含一个无效干扰项；修复版保留已通过选项，只替换坏项。"""

    def __init__(self) -> None:
        self.enrich_calls = 0
        self.judge_calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        text = "\n".join(message.content for message in messages)
        if role == "enrich":
            self.enrich_calls += 1
            options = (
                ["变量本身", "值的快照", "外层作用域", "调用时环境"]
                if self.enrich_calls == 1
                else ["变量本身", "值的快照", "动态作用域", "调用时环境"]
            )
            return Completion(
                text=_mc_json(options=options, answer_index=0),
                usage=Usage(prompt_tokens=5, completion_tokens=2),
            )
        self.judge_calls += 1
        if "待评干扰项：外层作用域" in text:
            label = "无效干扰"
        elif "待评干扰项：动态作用域" in text:
            label = "较弱干扰"
        else:
            label = "合理干扰"
        return Completion(
            text=json.dumps({"label": label, "rationale": "测试理由"}, ensure_ascii=False),
            usage=Usage(prompt_tokens=5, completion_tokens=2),
        )


async def test_quality_policy_repairs_only_rejected_distractor_and_records_trace() -> None:
    provider = _RepairingDistractorProvider()
    emitter, events = _emitter()

    mc = await generate_multiple_choice(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        max_attempts=3,
        num_options=4,
        quality_policy=DistractorQualityPolicy(
            minimum_label="较弱干扰",
            minimum_reasonable=2,
        ),
    )

    assert list(mc.options) == ["变量本身", "值的快照", "动态作用域", "调用时环境"]
    assert provider.enrich_calls == 2
    assert provider.judge_calls == 4  # 首版 3 项 + 只评新替换的 1 项
    event_types = [event.type for event in events]
    assert "learning.multiple_choice_generation.started" in event_types
    rejected = next(
        event
        for event in events
        if event.type == "learning.multiple_choice_generation.attempt_rejected"
    )
    assert rejected.payload["reason_code"] == "distractor_quality_unmet"
    assert rejected.payload["retained_distractor_count"] == 2
    ended = next(
        event for event in events if event.type == "learning.multiple_choice_generation.ended"
    )
    assert ended.payload == {
        "ok": True,
        "attempts": 2,
        "repair_attempts": 1,
        "judge_calls": 4,
    }
    assert [event.seq for event in events] == list(range(len(events)))
