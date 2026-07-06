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

from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.domain.learning.question import (
    MultipleChoiceQuestion,
    QuestionError,
    generate_multiple_choice,
)
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

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        self.roles.append(role)
        return Completion(text=self.text, usage=Usage(prompt_tokens=5, completion_tokens=2))


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


def _item() -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id="res",
        index=0,
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
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]
