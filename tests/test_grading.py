"""判卷结构化输出契约测试（缝 3）——注入假 provider，无真实 LLM。

被测：合法 verdict JSON → Verdict（三种 verdict 都能解析）；verdict 非法枚举值 / cited_evidence
为空 → 有界重试用尽 GradingError（provider 被多调）；判卷走 role=basic。
"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.assessment.grading import (
    GradingError,
    Verdict,
    build_answer_evidence_units,
    grade_answer,
    grade_multiple_choice,
    grading_prompt_version,
)
from grandquiz.domain.learning.assessment.question import (
    ExpectedPoint,
    MultipleChoiceQuestion,
    QuestionSpec,
)
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, Usage

_QUOTE = "闭包捕获的是变量而非值"
_POINT_CAPTURE = "capture"
_POINT_CONTRAST = "contrast"


def test_answer_evidence_units_are_stable_exact_and_shown_once() -> None:
    answer = "  第一条。\n第二条！ Third?  "

    units = build_answer_evidence_units(answer)

    assert [unit.model_dump() for unit in units] == [
        {"unit_id": "v1e002_006", "start": 2, "end": 6, "text": "第一条。"},
        {"unit_id": "v1e007_011", "start": 7, "end": 11, "text": "第二条！"},
        {"unit_id": "v1e012_018", "start": 12, "end": 18, "text": "Third?"},
    ]
    assert all(answer[unit.start : unit.end] == unit.text for unit in units)


def _question_spec() -> QuestionSpec:
    return QuestionSpec(
        question="什么是闭包？",
        expected_points=[
            ExpectedPoint(
                point_id=_POINT_CAPTURE,
                description="说明闭包捕获变量本身",
                cited_evidence=_QUOTE,
            ),
            ExpectedPoint(
                point_id=_POINT_CONTRAST,
                description="明确不是捕获值快照",
                cited_evidence=_QUOTE,
            ),
        ],
        reference_answer="闭包捕获变量本身，而不是值快照。",
        cited_evidence=[_QUOTE],
    )


def test_grading_prompt_version_tracks_legacy_and_claim_contracts() -> None:
    legacy = _question_spec()
    claim_aware = legacy.model_copy(
        update={
            "expected_points": [
                point.model_copy(update={"required_claims": [point.description]})
                for point in legacy.expected_points
            ]
        }
    )

    assert grading_prompt_version(legacy).startswith("answer_grade@")
    assert grading_prompt_version(claim_aware).startswith("answer_grade_claims@")


def _verdict_json(
    verdict: str = "对",
    *,
    matched_points: list[str] | None = None,
    missing_points: list[str] | None = None,
    diagnosis: str = "complete",
    reason: str = "回答覆盖了全部评分点。",
    cited_evidence: object = None,
) -> str:
    effective_matched = (
        [_POINT_CAPTURE, _POINT_CONTRAST] if matched_points is None else matched_points
    )
    effective_missing = [] if missing_points is None else missing_points
    return json.dumps(
        {
            "verdict": verdict,
            "point_assessments": [
                {
                    "point_id": point_id,
                    "label": "matched" if point_id in effective_matched else "missing",
                    "answer_evidence_ids": (
                        ["v1e000_009"] if point_id in effective_matched else []
                    ),
                    "reason": "测试用逐点评判。",
                }
                for point_id in [*effective_matched, *effective_missing]
            ],
            "diagnosis": diagnosis,
            "reason": reason,
            "cited_evidence": [_QUOTE] if cited_evidence is None else cited_evidence,
        },
        ensure_ascii=False,
    )


class _FixedProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.roles: list[Role] = []
        self.messages: list[list[Message]] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        self.messages.append(list(messages))
        return Completion(text=self.text, usage=Usage(prompt_tokens=5, completion_tokens=2))


class _EvidenceRepairProvider:
    """先返回未知 Evidence ID；收到可操作反馈后改为有效 ID。"""

    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[list[Message]] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        del role, tools
        self.calls += 1
        self.messages.append(list(messages))
        payload = json.loads(_verdict_json())
        if self.calls == 1:
            payload["point_assessments"][0]["answer_evidence_ids"] = ["v1e999_1000"]
        else:
            retry_note = messages[-1].content
            assert "v1e999_1000" in retry_note
            assert "v1e000_009" in retry_note
            assert "只能选择" in retry_note
            payload["point_assessments"][0]["answer_evidence_ids"] = ["v1e000_009"]
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=5, completion_tokens=2),
        )


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


async def _grade(provider: _FixedProvider, *, max_attempts: int = 3) -> Verdict:
    emitter, _ = _emitter()
    return await grade_answer(
        _question_spec(),
        "闭包能捕获外层变量",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        max_attempts=max_attempts,
    )


async def test_valid_verdict_parses() -> None:
    provider = _FixedProvider(_verdict_json())
    emitter, events = _emitter()

    verdict = await grade_answer(
        _question_spec(),
        "闭包能捕获外层变量",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
    )

    assert isinstance(verdict, Verdict)
    assert verdict.verdict == "对"
    assert verdict.matched_points == [_POINT_CAPTURE, _POINT_CONTRAST]
    assert verdict.missing_points == []
    assert verdict.diagnosis == "complete"
    assert verdict.cited_evidence == [_QUOTE]
    assert provider.calls == 1
    assert provider.roles == ["basic"]  # 判卷走 basic 角色
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]


async def test_point_assessments_derive_partition_and_bind_exact_answer_evidence() -> None:
    learner_answer = "闭包捕获外层变量本身。它不是创建时的值快照。"
    provider = _FixedProvider(
        json.dumps(
            {
                "verdict": "对",
                "point_assessments": [
                    {
                        "point_id": _POINT_CAPTURE,
                        "label": "matched",
                        "answer_evidence_ids": ["v1e000_011"],
                        "reason": "明确指出捕获对象是变量本身。",
                    },
                    {
                        "point_id": _POINT_CONTRAST,
                        "label": "matched",
                        "answer_evidence_ids": ["v1e011_022"],
                        "reason": "明确排除了值快照。",
                    },
                ],
                "diagnosis": "complete",
                "reason": "两个原子评分点都有学习者原文支持。",
                "cited_evidence": [_QUOTE],
            },
            ensure_ascii=False,
        )
    )
    emitter, _ = _emitter()

    verdict = await grade_answer(
        _question_spec(),
        learner_answer,
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        max_attempts=1,
    )

    assert verdict.matched_points == [_POINT_CAPTURE, _POINT_CONTRAST]
    assert verdict.missing_points == []
    assert [point.answer_evidence for point in verdict.point_assessments] == [
        "闭包捕获外层变量本身。",
        "它不是创建时的值快照。",
    ]
    assert [point.answer_evidence_ids for point in verdict.point_assessments] == [
        ["v1e000_011"],
        ["v1e011_022"],
    ]


async def test_claim_aware_grading_binds_each_claim_and_uses_code_all_of() -> None:
    question = QuestionSpec(
        question="闭包捕获的对象是什么？",
        expected_points=[
            ExpectedPoint(
                point_id=_POINT_CAPTURE,
                description="说明捕获对象并排除快照语义",
                required_claims=["捕获变量本身", "不是定义时的值快照"],
                cited_evidence=_QUOTE,
            )
        ],
        reference_answer="闭包捕获变量本身，而不是值快照。",
        cited_evidence=[_QUOTE],
    )
    provider = _FixedProvider(
        json.dumps(
            {
                "verdict": "错",
                "point_assessments": [
                    {
                        "point_id": _POINT_CAPTURE,
                        "label": "missing",
                        "answer_evidence_ids": [],
                        "claim_assessments": [
                            {
                                "claim_id": "capture.claim_2",
                                "label": "missing",
                                "answer_evidence_ids": [],
                                "reason": "没有排除值快照。",
                            },
                            {
                                "claim_id": "capture.claim_1",
                                "label": "matched",
                                "answer_evidence_ids": ["v1e000_009"],
                                "reason": "直接说明捕获变量本身。",
                            },
                        ],
                        "reason": "第二个必要条件缺失。",
                    }
                ],
                "diagnosis": "missing_key_point",
                "reason": "只覆盖了捕获对象。",
                "cited_evidence": [_QUOTE],
            },
            ensure_ascii=False,
        )
    )
    emitter, _ = _emitter()

    verdict = await grade_answer(
        question,
        "闭包捕获变量本身。",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        max_attempts=1,
    )

    assert verdict.verdict == "错"
    assert verdict.missing_points == [_POINT_CAPTURE]
    claims = verdict.point_assessments[0].claim_assessments
    assert [claim.claim_id for claim in claims] == ["capture.claim_1", "capture.claim_2"]
    assert [claim.label for claim in claims] == ["matched", "missing"]
    assert claims[0].answer_evidence == "闭包捕获变量本身。"
    system_prompt, user_prompt = [message.content for message in provider.messages[0]]
    assert "固定 `all-of`" in system_prompt
    assert "capture.claim_1" in user_prompt
    assert "capture.claim_2" in user_prompt
    assert "本题参考作答" not in user_prompt


async def test_legacy_point_lists_without_answer_evidence_are_rejected() -> None:
    provider = _FixedProvider(
        json.dumps(
            {
                "verdict": "对",
                "matched_points": [_POINT_CAPTURE, _POINT_CONTRAST],
                "missing_points": [],
                "diagnosis": "complete",
                "reason": "旧输出没有逐点答案证据。",
                "cited_evidence": [_QUOTE],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(GradingError, match="point_assessments"):
        await _grade(provider, max_attempts=1)


async def test_model_output_rejects_copied_answer_evidence_compatibility_field() -> None:
    payload = json.loads(_verdict_json())
    payload["point_assessments"][0]["answer_evidence"] = "学习者根本没有写过这句"
    provider = _FixedProvider(json.dumps(payload, ensure_ascii=False))

    with pytest.raises(GradingError, match="answer_evidence"):
        await _grade(provider, max_attempts=1)


async def test_missing_point_rejects_fabricated_answer_evidence() -> None:
    payload = json.loads(
        _verdict_json(
            "勉强",
            matched_points=[_POINT_CAPTURE],
            missing_points=[_POINT_CONTRAST],
            diagnosis="missing_key_point",
        )
    )
    payload["point_assessments"][1]["answer_evidence"] = "闭包"
    provider = _FixedProvider(json.dumps(payload, ensure_ascii=False))

    with pytest.raises(GradingError, match="schema"):
        await _grade(provider, max_attempts=1)


async def test_grader_prompt_requires_semantic_entailment_without_inventing_support() -> None:
    provider = _FixedProvider(_verdict_json())

    await _grade(provider)

    system_prompt = provider.messages[0][0].content
    user_prompt = provider.messages[0][1].content
    assert "允许同义改写" in system_prompt
    assert "不得脑补" in system_prompt
    assert "必要条件" in system_prompt
    assert "操作机制" in system_prompt
    assert "分流" in system_prompt
    assert "reason` 不超过 30" in system_prompt
    assert "总体 `reason` 不超过 50" in system_prompt
    assert "answer_evidence" in system_prompt
    assert "point_assessments" in user_prompt


async def test_grader_prompt_never_trades_exact_evidence_for_an_80_char_target() -> None:
    provider = _FixedProvider(_verdict_json())

    await _grade(provider)

    system_prompt = provider.messages[0][0].content
    assert "不超过 80" not in system_prompt
    assert "answer_evidence_ids" in system_prompt
    assert "不得复制、改写或创造" in system_prompt
    assert "为空列表" in system_prompt


async def test_unknown_evidence_id_gets_actionable_retry_and_recovers() -> None:
    provider = _EvidenceRepairProvider()
    emitter, _ = _emitter()

    verdict = await grade_answer(
        _question_spec(),
        "闭包能捕获外层变量",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
    )

    assert verdict.verdict == "对"
    assert provider.calls == 2


async def test_string_cited_evidence_is_coerced_to_list() -> None:
    # 真机 LLM 常把单条 cited_evidence 写成裸字符串（正是这次真机踩到的 list_type 报错）——
    # 被宽容纳成单元素列表，锚定门在其后照常把关。
    provider = _FixedProvider(
        _verdict_json(
            "错",
            matched_points=[],
            missing_points=[_POINT_CAPTURE, _POINT_CONTRAST],
            diagnosis="wrong_focus",
            reason="回答偏离了题目要求。",
            cited_evidence=_QUOTE,
        )
    )
    verdict = await _grade(provider)
    assert verdict.cited_evidence == [_QUOTE]
    assert provider.calls == 1  # 裸字符串被纳成列表 + 引文命中真实证据 → 无需重试


async def test_substring_citation_is_accepted() -> None:
    # 判卷锚定门放宽为子串（与出题门对称）：判卷只引长证据里一句短句，仍属真实原文，首次即过。
    provider = _FixedProvider(_verdict_json(cited_evidence=["捕获的是变量"]))
    verdict = await _grade(provider)
    assert verdict.cited_evidence == ["捕获的是变量"]
    assert provider.calls == 1  # 子串命中真实证据 → 无需重试


@pytest.mark.parametrize("label", ["对", "勉强", "错"])
async def test_all_three_verdicts_parse(label: str) -> None:
    payloads = {
        "对": _verdict_json(),
        "勉强": _verdict_json(
            "勉强",
            matched_points=[_POINT_CAPTURE],
            missing_points=[_POINT_CONTRAST],
            diagnosis="missing_key_point",
            reason="答到了捕获变量，但没明确排除值快照。",
        ),
        "错": _verdict_json(
            "错",
            matched_points=[],
            missing_points=[_POINT_CAPTURE, _POINT_CONTRAST],
            diagnosis="wrong_focus",
            reason="回答没有触及本题评分点。",
        ),
    }
    provider = _FixedProvider(payloads[label])
    verdict = await _grade(provider)
    assert verdict.verdict == label


async def test_reason_is_parsed_when_present() -> None:
    # 判官一句话诊断进 reason（只展示、不驱动记账）。
    provider = _FixedProvider(
        _verdict_json(
            "勉强",
            matched_points=[_POINT_CAPTURE],
            missing_points=[_POINT_CONTRAST],
            diagnosis="missing_key_point",
            reason="方向对但没点出不是值快照",
        )
    )
    verdict = await _grade(provider)
    assert verdict.reason == "方向对但没点出不是值快照"


async def test_code_derives_wrong_when_a_critical_point_is_missing() -> None:
    question = _question_spec().model_copy(update={"critical_point_ids": [_POINT_CONTRAST]})
    provider = _FixedProvider(
        _verdict_json(
            "勉强",
            matched_points=[_POINT_CAPTURE],
            missing_points=[_POINT_CONTRAST],
            diagnosis="missing_key_point",
        )
    )
    emitter, _ = _emitter()

    verdict = await grade_answer(
        question,
        "闭包能捕获外层变量",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
    )

    assert verdict.model_verdict == "勉强"
    assert verdict.verdict == "错"


async def test_code_derives_partial_when_no_declared_critical_point_is_missing() -> None:
    provider = _FixedProvider(
        _verdict_json(
            "错",
            matched_points=[_POINT_CAPTURE],
            missing_points=[_POINT_CONTRAST],
            diagnosis="wrong_focus",
        )
    )

    verdict = await _grade(provider)

    assert verdict.model_verdict == "错"
    assert verdict.verdict == "勉强"


async def test_partial_answer_must_name_both_matched_and_missing_points() -> None:
    provider = _FixedProvider(
        _verdict_json(
            "勉强",
            matched_points=[_POINT_CAPTURE],
            missing_points=[],
            diagnosis="missing_key_point",
            reason="漏了一个要点。",
        )
    )
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=1)


async def test_illegal_verdict_enum_retries_then_raises() -> None:
    # verdict 非三值枚举 → schema 校验失败 → ModelRetry 用尽 → GradingError。
    provider = _FixedProvider(_verdict_json("满分"))
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


async def test_empty_cited_evidence_retries_then_raises() -> None:
    # 判卷校验门：cited_evidence 为空 → ModelRetry 用尽 → GradingError。
    provider = _FixedProvider(_verdict_json(cited_evidence=[]))
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


async def test_fabricated_cited_evidence_retries_then_raises() -> None:
    # 判卷锚定门（与出题门对称）：引了伪造的"原文" → ModelRetry 用尽 → GradingError。
    provider = _FixedProvider(_verdict_json(cited_evidence=["这句原文根本不存在"]))
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


async def test_malformed_json_retries_then_raises() -> None:
    provider = _FixedProvider("这不是 JSON")
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


class _RaisingProvider:
    """provider.complete 抛传输类异常（模拟网络 / 超时 / 5xx，或 ReplayMiss）。计被调次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        raise RuntimeError("网络超时")


# --- M3.4 选择题确定性判卷（缝 2，纯代码不调 LLM）------------------------------------


def _mc() -> MultipleChoiceQuestion:
    return MultipleChoiceQuestion(
        question="闭包捕获的是？",
        options=["值的快照", "变量本身", "函数体"],
        answer_index=1,
        cited_evidence=[_QUOTE],
    )


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        ("变量本身", "对"),  # == options[answer_index] → 对
        ("值的快照", "错"),  # 其它选项 → 错
        ("函数体", "错"),
        ("压根不在选项里的文本", "错"),  # 非选项文本 → 错（MC 无"勉强"）
    ],
)
def test_grade_multiple_choice_is_deterministic(chosen: str, expected: str) -> None:
    # 确定性判卷：所选项文本与正确项逐字比对，纯代码、不构造任何 provider / emitter。
    assert grade_multiple_choice(chosen, _mc()) == expected


async def test_provider_exception_closes_model_span_and_propagates() -> None:
    # provider 基础设施异常：先发 MODEL_ENDED(ok=False) 闭合 span，再原样冒泡（不吞、不重试）。
    provider = _RaisingProvider()
    emitter, events = _emitter()

    with pytest.raises(RuntimeError):
        await grade_answer(
            _question_spec(),
            "闭包能捕获外层变量",
            provider=provider,
            emitter=emitter,
            parent_span_id="a",
        )

    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]
    assert events[-1].payload["ok"] is False
    assert provider.calls == 1
