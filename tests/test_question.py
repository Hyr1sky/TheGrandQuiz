"""出题结构化输出契约测试（缝 3）——注入假 provider，无真实 LLM。

被测：合法输出 → GeneratedQuestion；cited_evidence 为空 / 引了不属于该 item 的伪造引文
→ 有界重试用尽 QuestionError（provider 被多调，证明发生重试）；provider 传输异常 →
闭合 model span(ok=False) 后原样冒泡、不重试（防吞掉 harness 错误）。
"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.assessment.question import (
    ExpectedPoint,
    GeneratedQuestion,
    MultipleChoiceQuestion,
    QuestionError,
    QuestionSpec,
    generate_multiple_choice,
    generate_question,
)
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, Usage

_QUOTE = "闭包捕获的是变量而非值"
_POINT_ID = "capture-semantics"


def _question_json(**overrides: object) -> str:
    payload: dict[str, object] = {
        "question": "什么是闭包？",
        "expected_points": [
            {
                "point_id": _POINT_ID,
                "description": "说明闭包捕获变量本身，而不是值快照",
                "cited_evidence": _QUOTE,
            }
        ],
        "reference_answer": "闭包捕获的是变量本身，而不是定义时的值快照。",
        "cited_evidence": [_QUOTE],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class _FixedProvider:
    """返回固定文本、计被调次数、记录每次 role。``role`` 接收后用于断言两槽角色。"""

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


async def test_valid_output_becomes_generated_question() -> None:
    provider = _FixedProvider(_question_json())
    emitter, events = _emitter()

    question = await generate_question(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )

    assert isinstance(question, GeneratedQuestion)
    assert isinstance(question, QuestionSpec)
    assert question.question == "什么是闭包？"
    assert question.expected_points == [
        ExpectedPoint(
            point_id=_POINT_ID,
            description="说明闭包捕获变量本身，而不是值快照",
            cited_evidence=_QUOTE,
        )
    ]
    assert question.reference_answer == "闭包捕获的是变量本身，而不是定义时的值快照。"
    assert question.cited_evidence == [_QUOTE]
    assert provider.calls == 1  # 首次即通过，无重试
    assert provider.roles == ["enrich"]  # 出题走 enrich 角色
    # 照 reader 的 model span 模式发了一对 MODEL_STARTED / MODEL_ENDED
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]


async def test_string_cited_evidence_is_coerced_to_list() -> None:
    # 真机 LLM 常把单条 cited_evidence 写成裸字符串——被宽容纳成单元素列表，锚定门在其后照常把关。
    provider = _FixedProvider(_question_json(cited_evidence=_QUOTE))
    emitter, _ = _emitter()

    question = await generate_question(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert question.cited_evidence == [_QUOTE]
    assert provider.calls == 1  # 裸字符串被纳成列表 + 引文命中真实证据 → 无需重试


async def test_empty_cited_evidence_retries_then_raises() -> None:
    # 校验门：cited_evidence 为空 → ModelRetry 用尽 → QuestionError（provider 被多调）。
    provider = _FixedProvider(_question_json(cited_evidence=[]))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_question(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2  # > 1 即证明发生了重试


async def test_forged_citation_is_rejected_as_ghost_question() -> None:
    # 校验门（防幽灵题）：引了不属于该 item 的伪造引文 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(_question_json(cited_evidence=["这句话材料里根本没有"]))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_question(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=3
        )
    assert provider.calls == 3  # 伪造引文持续被拒 → 重试用尽


async def test_substring_citation_is_accepted() -> None:
    # 锚定门放宽为子串（真机 dogfood 坑）：出题只引长证据里一句短句，仍属真实原文，首次即过。
    # 旧门要整条 evidence 全等，把合法子串误判成幽灵引文、重试用尽后崩溃。
    provider = _FixedProvider(
        _question_json(
            expected_points=[
                {
                    "point_id": _POINT_ID,
                    "description": "说明捕获语义",
                    "cited_evidence": "捕获的是变量",
                }
            ],
            cited_evidence=["捕获的是变量"],
        )
    )
    emitter, _ = _emitter()

    question = await generate_question(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert question.cited_evidence == ["捕获的是变量"]
    assert provider.calls == 1  # 子串命中真实证据 → 无需重试


async def test_open_question_requires_an_explicit_grounded_rubric() -> None:
    provider = _FixedProvider(json.dumps({"question": "什么是闭包？", "cited_evidence": [_QUOTE]}))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_question(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=1
        )


async def test_malformed_json_retries_then_raises() -> None:
    provider = _FixedProvider("这不是 JSON")
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_question(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


class _RaisingProvider:
    """complete 抛传输类异常（模拟网络 / 超时 / 5xx，或 ReplayMiss）。计被调次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        raise RuntimeError("网络超时")


async def test_provider_exception_closes_model_span_and_propagates() -> None:
    # provider 基础设施异常：先发 MODEL_ENDED(ok=False) 闭合 span，再原样冒泡、不重试、不吞成
    # QuestionError（否则会把 ReplayMiss 等 harness 错误静默掩盖）。
    provider = _RaisingProvider()
    emitter, events = _emitter()

    with pytest.raises(RuntimeError):
        await generate_question(_item(), provider=provider, emitter=emitter, parent_span_id="a")

    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]
    assert events[-1].payload["ok"] is False
    assert provider.calls == 1  # 基础设施异常不重试


# --- M3.4 选择题出题（缝 3，MC 校验门）+ 追问 prompt 变体 ------------------------------


def _mc_json(**overrides: object) -> str:
    payload: dict[str, object] = {
        "question": "闭包捕获的是什么？",
        "options": ["值的快照", "变量本身"],
        "answer_index": 1,
        "cited_evidence": [_QUOTE],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


async def test_valid_mc_output_becomes_multiple_choice() -> None:
    provider = _FixedProvider(_mc_json())
    emitter, events = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )

    assert isinstance(mc, MultipleChoiceQuestion)
    assert mc.options == ["值的快照", "变量本身"]
    assert mc.answer_index == 1
    assert mc.cited_evidence == [_QUOTE]
    assert provider.calls == 1  # 首次即通过，无重试
    assert provider.roles == ["enrich"]  # MC 出题也走 enrich 角色
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]


async def test_mc_too_few_options_retries_then_raises() -> None:
    # 可判卷门：options < 2 → 无从比对 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(_mc_json(options=["只有一项"], answer_index=0))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_mc_answer_index_out_of_range_retries_then_raises() -> None:
    # 可判卷门：answer_index 越界 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(_mc_json(answer_index=5))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_mc_empty_cited_evidence_retries_then_raises() -> None:
    # 防幽灵题门：cited_evidence 为空 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(_mc_json(cited_evidence=[]))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_mc_forged_citation_is_rejected_as_ghost_question() -> None:
    # 防幽灵题门：引了不属于该 item 的伪造引文 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(_mc_json(cited_evidence=["这句话材料里根本没有"]))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=3
        )
    assert provider.calls == 3


async def test_mc_substring_citation_is_accepted() -> None:
    # 锚定门放宽为子串（与开放题同规则）：引真实证据的一句短句 → 首次即过、无重试。
    provider = _FixedProvider(_mc_json(cited_evidence=["捕获的是变量"]))
    emitter, _ = _emitter()

    mc = await generate_multiple_choice(
        _item(), provider=provider, emitter=emitter, parent_span_id="a"
    )
    assert mc.cited_evidence == ["捕获的是变量"]
    assert provider.calls == 1  # 子串命中真实证据 → 无需重试


async def test_mc_empty_option_retries_then_raises() -> None:
    # 可判卷门：空 / 纯空白选项 → NonEmptyStr 拒 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(_mc_json(options=["", "变量本身"], answer_index=1))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_mc_duplicate_options_retries_then_raises() -> None:
    # 可判卷门：选项重复 → 文本比对判卷会被骗 → ModelRetry 用尽 → QuestionError。
    provider = _FixedProvider(_mc_json(options=["变量本身", "变量本身"], answer_index=0))
    emitter, _ = _emitter()

    with pytest.raises(QuestionError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a", max_attempts=2
        )
    assert provider.calls == 2


async def test_mc_provider_exception_closes_model_span_and_propagates() -> None:
    # MC 出题同样：provider 基础设施异常 → 发 MODEL_ENDED(ok=False) 闭合 span，再原样冒泡、不重试。
    provider = _RaisingProvider()
    emitter, events = _emitter()

    with pytest.raises(RuntimeError):
        await generate_multiple_choice(
            _item(), provider=provider, emitter=emitter, parent_span_id="a"
        )
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]
    assert events[-1].payload["ok"] is False
    assert provider.calls == 1


async def test_probe_prompt_variant_reflected_in_trace_prompt_version() -> None:
    # 追问复用 generate_question，仅换 prompt——断 model span 的 prompt_version 反映 probe 变体，
    # 故 trace 能把追问题与标准开放题区分归因（eval 回归可定位到具体题型 prompt）。
    provider = _FixedProvider(_question_json(question="为什么？"))
    emitter, events = _emitter()

    question = await generate_question(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        prompt_name="question_probe",
    )

    assert isinstance(question, GeneratedQuestion)  # 追问与开放共用 schema
    started = next(e for e in events if e.type == EventType.MODEL_STARTED)
    assert str(started.payload["prompt_version"]).startswith("question_probe@")


# --- SE-S6：开放 / 追问难度软杠杆（difficulty_hint 传入才追加；None 时逐字节等价改动前）----------
# 软性如实标注：这里只断言"hint 非 None 时被追加进 messages / None 时一字不追加"，**不断言**"高档
# 题真的更难"（深度主观、超出确定性可断言范围，见 difficulty.difficulty_prompt_hint）。

_HINT_SENTINEL = "【SE-S6 难度提示占位·请问边界与反例】"


class _MessageCapturingProvider:
    """返回固定文本、留存最后一次收到的 messages（断言难度提示被 / 未被追加）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.last_messages: list[Message] = []
        self.last_text = ""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.last_messages = list(messages)
        self.last_text = "\n".join(m.content for m in messages)
        return Completion(text=self.text, usage=Usage(prompt_tokens=5, completion_tokens=2))


async def test_difficulty_hint_appended_to_open_question() -> None:
    # 传 difficulty_hint → 发出的 messages 含该 hint（作为一条 user message 追加）。
    provider = _MessageCapturingProvider(_question_json())
    emitter, _ = _emitter()

    await generate_question(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        difficulty_hint=_HINT_SENTINEL,
    )

    assert provider.calls == 1
    assert _HINT_SENTINEL in provider.last_text  # 难度提示确实注入了出题请求
    # 追加为一条独立 user message（内容恰为 hint），且在 system + item + (无 asked_before) 之后。
    hint_msgs = [m for m in provider.last_messages if m.content == _HINT_SENTINEL]
    assert len(hint_msgs) == 1
    assert hint_msgs[0].role == "user"


async def test_difficulty_hint_appended_to_probe_question() -> None:
    # 追问（prompt_name=question_probe）走同一入口 → 难度提示同样被追加（开放 + 追问都覆盖）。
    provider = _MessageCapturingProvider(_question_json(question="为什么？"))
    emitter, _ = _emitter()

    await generate_question(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        prompt_name="question_probe",
        difficulty_hint=_HINT_SENTINEL,
    )

    assert provider.calls == 1
    assert _HINT_SENTINEL in provider.last_text  # 追问路径同样注入难度提示


async def test_difficulty_hint_none_is_byte_equivalent_to_absent() -> None:
    # **关键对照**：显式传 difficulty_hint=None 与完全不传 → 发出的 messages 逐字节相同（证明默认
    # 路径不追加任何难度约束；这是 eval / cassette 字节等价的命根）。开放与追问都验一遍。
    q_json = _question_json()
    for prompt_name in ("question_generate", "question_probe"):
        absent = _MessageCapturingProvider(q_json)
        emitter_a, _ = _emitter()
        await generate_question(
            _item(),
            provider=absent,
            emitter=emitter_a,
            parent_span_id="a",
            prompt_name=prompt_name,
        )
        none_arg = _MessageCapturingProvider(q_json)
        emitter_b, _ = _emitter()
        await generate_question(
            _item(),
            provider=none_arg,
            emitter=emitter_b,
            parent_span_id="a",
            prompt_name=prompt_name,
            difficulty_hint=None,
        )
        # 逐字节等价：两次发出的 messages 完全相同（role + content 均一致）。
        assert [m.model_dump() for m in absent.last_messages] == [
            m.model_dump() for m in none_arg.last_messages
        ]
        # 且既有默认路径不含任何难度提示占位。
        assert _HINT_SENTINEL not in none_arg.last_text
