"""判卷工具——照 Reader 的 LLM 槽模式，对一次作答产出结构化判决（缝 3）。

ADR-0004 的两个 LLM 槽之二：判卷走 **role=basic**（deepseek），对每个原子
评分点产出语义命中标签、学习者答案 Evidence 单元 ID 与理由。**LLM 判卷，代码记账**：
LLM 负责逐点语义判断；代码把答案确定性切成唯一单元、校验模型选择，并按 critical point
确定性聚合三值判决。``weak_item_id`` 仍由调用方（``assess_once``）按聚合后的
``verdict`` 用代码算，**不由 LLM 产**——记账必须确定、可 eval、可回放。

与出题同源的三条约束（结构化输出契约 / 判卷校验门 / 事件上脊柱）见 ``question.py`` 模块 docstring。
判卷校验门（缝 3，与出题门对称）：``cited_evidence`` 非空，且每条引文都锚定被考 item 的
``evidence.quote``（是其子串即可，见 ``ungrounded_citations``）——判卷必须锚定真实证据、可复查；
空 / 幽灵引文 → ``ModelRetry``。
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from grandquiz.domain.learning.assessment.question import MultipleChoiceQuestion, QuestionSpec
from grandquiz.domain.learning.assessment.workflow import GRADE_ANSWER
from grandquiz.domain.learning.models import CitedEvidence, ungrounded_citations
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.kernel.recovery import ErrorClass
from grandquiz.providers.base import Completion, Message, Provider

# 判决三值——出题 / 判卷 / 记账全链路共用的枚举（assessment 的 AssessmentResult 亦复用之）。
VerdictLabel = Literal["对", "勉强", "错"]
OpenAnswerDiagnosisKind = Literal[
    "complete",
    "missing_key_point",
    "wrong_focus",
    "concept_confusion",
    "off_topic",
    "uncertain",
]
AssessmentDiagnosisKind = Literal[
    "complete",
    "missing_key_point",
    "wrong_focus",
    "concept_confusion",
    "off_topic",
    "uncertain",
    "incorrect_choice",
]
PointAssessmentLabel = Literal["matched", "missing"]


class AnswerEvidenceUnit(BaseModel):
    """一次判卷内由代码生成、可按 ID 选择的版本化答案原文切片。"""

    model_config = ConfigDict(frozen=True)

    unit_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


def build_answer_evidence_units(answer: str) -> tuple[AnswerEvidenceUnit, ...]:
    """Split an answer once, in source order, into deterministic exact Evidence units."""

    ranges: list[tuple[int, int]] = []
    start = 0
    for index, character in enumerate(answer):
        if character not in "。！？!?\n":
            continue
        end = index + 1
        left, right = start, end
        while left < right and answer[left].isspace():
            left += 1
        while right > left and answer[right - 1].isspace():
            right -= 1
        if left < right:
            ranges.append((left, right))
        start = end
    left, right = start, len(answer)
    while left < right and answer[left].isspace():
        left += 1
    while right > left and answer[right - 1].isspace():
        right -= 1
    if left < right:
        ranges.append((left, right))
    return tuple(
        AnswerEvidenceUnit(
            unit_id=f"v1e{left:03d}_{right:03d}",
            start=left,
            end=right,
            text=answer[left:right],
        )
        for left, right in ranges
    )


def grade_multiple_choice(chosen: str, mc: MultipleChoiceQuestion) -> VerdictLabel:
    """选择题确定性判卷（纯函数、**不调 LLM**）：所选项文本 == 正确项 → ``对``，否则 ``错``。

    ADR-0004 / PRD："选择题确定性比对"——MC 不占 LLM 判卷槽，故 MC **判卷无判卷 model span、
    无需 cassette、更确定**（MC 出题槽仍打真实 LLM、录放照旧）。MC 只有两值（对 / 错），无"勉强"
    （选项非对即错）。``chosen`` 是
    responder 返回的所选项文本，与 ``mc.options[mc.answer_index]`` 逐字比对——记账（薄弱与否）仍
    由 ``assess_once`` 的代码按此 verdict 算，与开放题判卷统一走同一条记账路径。
    """
    return "对" if chosen == mc.options[mc.answer_index] else "错"


def derive_verdict(
    *,
    expected_point_ids: list[str],
    matched_point_ids: set[str],
    critical_point_ids: list[str],
) -> VerdictLabel:
    """按冻结的评分点与核心点确定性聚合三值判决。"""

    expected = set(expected_point_ids)
    matched = expected & matched_point_ids
    missing = expected - matched
    if not missing:
        return "对"
    if not matched or set(critical_point_ids) & missing:
        return "错"
    return "勉强"


def _stable_error_summary(exc: ValidationError) -> str:
    """把 pydantic 校验错误压成版本无关摘要（只取 loc + type）——理由同 reader / question。"""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in exc.errors())


class GradingError(Exception):
    """判卷失败——有界重试用尽仍拿不到合法 ``Verdict``。

    ``assess_once`` 视其为基础设施级失败、闭合 assessment span 后原样冒泡；``error_class``
    标 ``DEGRADED`` 示"本轮可恢复"——由 kernel ``RecoveryPolicy`` 统一裁决为跳过本轮。
    """

    error_class = ErrorClass.DEGRADED


class ModelRetry(Exception):
    """结构化输出 / 校验门失败的重试信号（借 pydantic-ai 语义，同 reader / question）。"""


class ClaimAssessment(BaseModel):
    """模型对一个 required claim 的判断，以及由代码解析的答案原文证据。"""

    claim_id: str = Field(min_length=1)
    label: PointAssessmentLabel
    answer_evidence_ids: list[str] = Field(default_factory=list)
    answer_evidence: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def _evidence_matches_the_label(self) -> "ClaimAssessment":
        if (
            self.label == "matched"
            and not self.answer_evidence_ids
            and self.answer_evidence is None
        ):
            raise ValueError("matched claim 必须提供学习者答案 Evidence")
        if self.label == "missing" and (
            self.answer_evidence_ids or self.answer_evidence is not None
        ):
            raise ValueError("missing claim 不得伪造学习者答案证据")
        return self


class PointAssessment(BaseModel):
    """模型对一个原子评分点的判断，以及由代码解析的答案原文证据。"""

    point_id: str = Field(min_length=1)
    label: PointAssessmentLabel
    answer_evidence_ids: list[str] = Field(default_factory=list)
    answer_evidence: str | None = Field(default=None, min_length=1)
    claim_assessments: list[ClaimAssessment] = Field(
        default_factory=list[ClaimAssessment],
        exclude_if=lambda value: not value,
    )
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def _evidence_matches_the_label(self) -> "PointAssessment":
        if (
            self.label == "matched"
            and not self.answer_evidence_ids
            and self.answer_evidence is None
            and not self.claim_assessments
        ):
            raise ValueError("matched point 必须提供学习者答案 Evidence")
        if self.label == "missing" and (
            self.answer_evidence_ids or self.answer_evidence is not None
        ):
            raise ValueError("missing point 不得伪造学习者答案证据")
        return self


class Verdict(BaseModel):
    """判卷输出：三值结论 + 逐评分点覆盖结果 + 受控诊断 + 答案单元证据。

    刻意不含 ``weak_item_id``——薄弱记账由代码按 ``verdict`` 算（ADR-0004），不由 LLM 产。
    ``point_assessments`` 必须不重不漏地覆盖 QuestionSpec 的 point_id；每个
    ``matched`` 判断必须选择代码生成的 ``answer_evidence_ids``；代码再解析出兼容读字段
    ``answer_evidence``。``matched_points`` /
    ``missing_points`` 是由逐点判断派生的兼容字段；三值结论再由代码按 critical point
    聚合。这样“对 / 勉强 / 错”既可解释，也不会被模型自行改写记账规则。
    """

    verdict: VerdictLabel
    model_verdict: VerdictLabel | None = None
    matched_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    point_assessments: list[PointAssessment] = Field(default_factory=list[PointAssessment])
    diagnosis: OpenAnswerDiagnosisKind
    reason: str
    cited_evidence: CitedEvidence


def grading_prompt_name(question: QuestionSpec) -> str:
    """Return the versioned prompt family selected by the question rubric contract."""

    return (
        "answer_grade_claims"
        if all(point.required_claims for point in question.expected_points)
        else "answer_grade"
    )


def grading_prompt_version(question: QuestionSpec) -> str:
    """Return the exact prompt version used to grade this QuestionSpec."""

    return load_prompt(grading_prompt_name(question)).version


async def grade_answer(
    question: QuestionSpec,
    answer: str,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    max_attempts: int = 3,
    language: str = "中文",
) -> Verdict:
    """依据 ``QuestionSpec`` 的唯一 rubric 判卷；持续失败 → ``GradingError``。

    学习者答案只在 Prompt 中按稳定、互不重叠的 ``AnswerEvidenceUnit`` 展示一次；模型只选择
    ID，不能复制或改写原文。ID 经代码校验后才解析成报告与 UI 可读的原文片段。

    ``max_attempts``：1 次初始调用 + 最多 ``max_attempts - 1`` 次重试（默认 3；测试可收紧）。
    ``language``：判决与反馈语言（默认"中文"，由 ``assess_once`` 从 task 下传）——用字面
    ``str.replace`` 把模板里的 ``{{LANGUAGE}}`` 哨兵换成它（**不用 str.format**：模板含 JSON
    schema 示例的字面花括号，format 会崩）。版本号跨语言稳定；只 message / replay_key 随语言变。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    uses_claim_contract = grading_prompt_name(question) == "answer_grade_claims"
    prompt = load_prompt(grading_prompt_name(question))
    valid_quotes = {point.cited_evidence for point in question.expected_points}
    expected_claim_ids: dict[str, tuple[str, ...]] | None = None
    if uses_claim_contract:
        expected_claim_ids = {
            point.point_id: tuple(
                f"{point.point_id}.claim_{index}"
                for index, _ in enumerate(point.required_claims, start=1)
            )
            for point in question.expected_points
        }
        rubric_lines: list[str] = []
        for point in question.expected_points:
            rubric_lines.append(f"- {point.point_id}: {point.description}")
            rubric_lines.append("  必须逐项支持的 required claims（固定 all-of）：")
            rubric_lines.extend(
                f"    - [{claim_id}] {claim}"
                for claim_id, claim in zip(
                    expected_claim_ids[point.point_id],
                    point.required_claims,
                    strict=True,
                )
            )
            rubric_lines.append(f"  原文依据：{point.cited_evidence}")
        rubric_block = "\n".join(rubric_lines)
    else:
        rubric_block = "\n".join(
            (f"- {point.point_id}: {point.description}\n  原文依据：{point.cited_evidence}")
            for point in question.expected_points
        )
    evidence_block = "\n".join(f"- {quote}" for quote in sorted(valid_quotes))
    answer_evidence_units = build_answer_evidence_units(answer)
    answer_evidence_block = "\n".join(
        f"- [{unit.unit_id}] {unit.text}" for unit in answer_evidence_units
    )
    base_messages = [
        Message(role="system", content=prompt.text.replace("{{LANGUAGE}}", language)),
        Message(
            role="user",
            content=(
                f"题目：{question.question}\n"
                "本题原子评分点（point_assessments 必须逐项覆盖，只能填写这些 point_id）：\n"
                f"{rubric_block}\n"
                + ("" if uses_claim_contract else f"本题参考作答：{question.reference_answer}\n")
                + "判卷可引用的原文证据（cited_evidence 只能逐字从这里选择，"
                "不能引用参考作答或学习者作答）：\n"
                f"{evidence_block}\n"
                "学习者作答 Evidence 单元（原文按顺序只展示一次；"
                "answer_evidence_ids 只能选择方括号中的 ID）：\n"
                f"{answer_evidence_block}"
            ),
        ),
    ]
    retry_note: str | None = None
    last_error = ""
    for _ in range(max_attempts):
        messages = list(base_messages)
        if retry_note is not None:
            messages.append(Message(role="user", content=retry_note))
        completion = await _call_model(
            messages,
            provider=provider,
            emitter=emitter,
            parent_span_id=parent_span_id,
            prompt_version=prompt.version,
        )
        try:
            return _parse(
                completion.text,
                valid_quotes,
                answer_evidence_units=answer_evidence_units,
                expected_point_ids=[point.point_id for point in question.expected_points],
                expected_claim_ids=expected_claim_ids,
                critical_point_ids=question.critical_point_ids,
            )
        except ModelRetry as exc:
            last_error = str(exc)
            evidence_contract = (
                "每个 claim_assessments 必须覆盖当前评分点的全部 claim_id；matched claim 的 "
                "answer_evidence_ids 至少一个，missing claim 必须为空；point 的 label 必须等于"
                "全部 claim label 的固定 all-of 结果。"
                if uses_claim_contract
                else "matched 的 answer_evidence_ids 至少一个，missing 时必须为空列表。"
            )
            retry_note = (
                f"上一次判卷无法采用：{exc}。请只返回合法 JSON；point_assessments 必须覆盖全部"
                f"评分点。{evidence_contract} Evidence ID 只能选择当前学习者答案单元，"
                "不得复制、改写或创造 Evidence；"
                f"当前可选 ID：{[unit.unit_id for unit in answer_evidence_units]}；"
                f"cited_evidence 只能逐字引用材料原文证据：{sorted(valid_quotes)}。"
            )
    raise GradingError(f"判卷失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")


async def _call_model(
    messages: list[Message],
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    prompt_version: str,
) -> Completion:
    # 照 reader._call_model：一对 MODEL_STARTED / MODEL_ENDED 共享 span_id；判卷走 role=basic。
    span_id = emitter.new_span_id()
    emitter.emit(
        EventType.MODEL_STARTED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload={
            "messages": [m.model_dump() for m in messages],
            "prompt_version": prompt_version,
            "role": "basic",
            "node_id": GRADE_ANSWER,
        },
    )
    try:
        completion = await provider.complete(messages, role="basic")
    except Exception as exc:
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=span_id,
            parent_span_id=parent_span_id,
            payload={"ok": False, "error": repr(exc), "node_id": GRADE_ANSWER},
        )
        raise
    emitter.emit(
        EventType.MODEL_ENDED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload={
            "ok": True,
            "output": completion.text,
            "usage": completion.usage.model_dump(),
            "node_id": GRADE_ANSWER,
        },
    )
    return completion


def _parse(
    text: str,
    valid_quotes: set[str],
    *,
    answer_evidence_units: tuple[AnswerEvidenceUnit, ...],
    expected_point_ids: list[str],
    expected_claim_ids: dict[str, tuple[str, ...]] | None,
    critical_point_ids: list[str],
) -> Verdict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        verdict = Verdict.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc
    # 判卷校验门（缝 3，与出题门对称）：cited_evidence 非空 + 每条锚定被考 item 证据（子串即可）。
    # 防判卷 LLM 引伪造 / 空串"原文"蒙混——判决必须锚定真实证据、可复查。
    if not verdict.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：判卷必须引用原文证据")
    ghost = ungrounded_citations(verdict.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
    expected = set(expected_point_ids)
    if not verdict.point_assessments:
        raise ModelRetry("point_assessments 不能为空：每个评分点都必须绑定逐点理由")
    assessed_ids = [assessment.point_id for assessment in verdict.point_assessments]
    if len(assessed_ids) != len(set(assessed_ids)):
        raise ModelRetry("point_assessments 中的 point_id 不得重复")
    unknown_assessments = set(assessed_ids) - expected
    if unknown_assessments:
        raise ModelRetry(f"逐点评判引用了不存在的评分点：{sorted(unknown_assessments)}")
    uncovered_assessments = expected - set(assessed_ids)
    if uncovered_assessments:
        raise ModelRetry(f"逐点评判必须覆盖全部评分点：{sorted(uncovered_assessments)}")
    units_by_id = {unit.unit_id: unit for unit in answer_evidence_units}
    resolved_assessments: list[PointAssessment] = []
    for assessment in verdict.point_assessments:
        if assessment.answer_evidence is not None:
            raise ModelRetry(
                "模型输出不得提供兼容读字段 answer_evidence，只能选择 answer_evidence_ids："
                f"point_id={assessment.point_id}"
            )
        if expected_claim_ids is not None:
            if assessment.answer_evidence_ids:
                raise ModelRetry(
                    "required claims 路径不得填写 point 级 answer_evidence_ids："
                    f"point_id={assessment.point_id}"
                )
            expected_claims = expected_claim_ids[assessment.point_id]
            assessed_claim_ids = [claim.claim_id for claim in assessment.claim_assessments]
            if len(assessed_claim_ids) != len(set(assessed_claim_ids)):
                raise ModelRetry(
                    f"claim_assessments 的 claim_id 不得重复：point_id={assessment.point_id}"
                )
            if set(assessed_claim_ids) != set(expected_claims):
                raise ModelRetry(
                    "claim_assessments 必须不重不漏地覆盖 required claims："
                    f"point_id={assessment.point_id}，expected={list(expected_claims)}"
                )
            resolved_claims: list[ClaimAssessment] = []
            for claim in assessment.claim_assessments:
                if claim.answer_evidence is not None:
                    raise ModelRetry(
                        "模型不得填写 claim.answer_evidence，只能选择 answer_evidence_ids："
                        f"claim_id={claim.claim_id}"
                    )
                claim_evidence_ids = claim.answer_evidence_ids
                if len(claim_evidence_ids) != len(set(claim_evidence_ids)):
                    raise ModelRetry(
                        f"claim answer_evidence_ids 不得重复：claim_id={claim.claim_id}"
                    )
                unknown_claim_evidence = set(claim_evidence_ids) - set(units_by_id)
                if unknown_claim_evidence:
                    raise ModelRetry(
                        "claim answer_evidence_ids 引用了不存在的学习者答案单元："
                        f"claim_id={claim.claim_id}，非法值={sorted(unknown_claim_evidence)}"
                    )
                selected_claim_units = sorted(
                    (units_by_id[unit_id] for unit_id in claim_evidence_ids),
                    key=lambda unit: unit.start,
                )
                resolved_claims.append(
                    claim.model_copy(
                        update={
                            "answer_evidence_ids": [unit.unit_id for unit in selected_claim_units],
                            "answer_evidence": (
                                "\n".join(unit.text for unit in selected_claim_units)
                                if selected_claim_units
                                else None
                            ),
                        }
                    )
                )
            resolved_by_id = {claim.claim_id: claim for claim in resolved_claims}
            resolved_claims = [resolved_by_id[claim_id] for claim_id in expected_claims]
            claim_labels = {claim.label for claim in resolved_claims}
            derived_label: PointAssessmentLabel = (
                "matched" if claim_labels == {"matched"} else "missing"
            )
            if assessment.label != derived_label:
                raise ModelRetry(
                    "point label 必须等于 required claims 的固定 all-of 结果："
                    f"point_id={assessment.point_id}，expected={derived_label}"
                )
            point_units = sorted(
                {
                    units_by_id[evidence_id].unit_id: units_by_id[evidence_id]
                    for claim in resolved_claims
                    for evidence_id in claim.answer_evidence_ids
                }.values(),
                key=lambda unit: unit.start,
            )
            resolved_assessments.append(
                assessment.model_copy(
                    update={
                        "answer_evidence_ids": (
                            [unit.unit_id for unit in point_units]
                            if derived_label == "matched"
                            else []
                        ),
                        "answer_evidence": (
                            "\n".join(unit.text for unit in point_units)
                            if derived_label == "matched" and point_units
                            else None
                        ),
                        "claim_assessments": resolved_claims,
                    }
                )
            )
            continue
        if assessment.claim_assessments:
            raise ModelRetry(
                f"旧版评分点不得填写 claim_assessments：point_id={assessment.point_id}"
            )
        evidence_ids = assessment.answer_evidence_ids
        if assessment.label == "matched" and not evidence_ids:
            raise ModelRetry(
                f"matched point 必须选择 answer_evidence_ids：point_id={assessment.point_id}"
            )
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ModelRetry(f"answer_evidence_ids 不得重复：point_id={assessment.point_id}")
        unknown_evidence_ids = set(evidence_ids) - set(units_by_id)
        if unknown_evidence_ids:
            raise ModelRetry(
                "answer_evidence_ids 引用了不存在的学习者答案单元："
                f"point_id={assessment.point_id}，非法值={sorted(unknown_evidence_ids)}"
            )
        selected_units = sorted(
            (units_by_id[unit_id] for unit_id in evidence_ids),
            key=lambda unit: unit.start,
        )
        resolved_assessments.append(
            assessment.model_copy(
                update={
                    "answer_evidence_ids": [unit.unit_id for unit in selected_units],
                    "answer_evidence": (
                        "\n".join(unit.text for unit in selected_units) if selected_units else None
                    ),
                }
            )
        )
    verdict = verdict.model_copy(update={"point_assessments": resolved_assessments})
    assessed_matched = [
        assessment.point_id
        for assessment in verdict.point_assessments
        if assessment.label == "matched"
    ]
    assessed_missing = [
        assessment.point_id
        for assessment in verdict.point_assessments
        if assessment.label == "missing"
    ]
    if verdict.matched_points and verdict.matched_points != assessed_matched:
        raise ModelRetry("matched_points 与 point_assessments 不一致")
    if verdict.missing_points and verdict.missing_points != assessed_missing:
        raise ModelRetry("missing_points 与 point_assessments 不一致")
    verdict = verdict.model_copy(
        update={
            "matched_points": assessed_matched,
            "missing_points": assessed_missing,
        }
    )
    matched = verdict.matched_points
    missing = verdict.missing_points
    if len(matched) != len(set(matched)) or len(missing) != len(set(missing)):
        raise ModelRetry("matched_points / missing_points 中的 point_id 不得重复")
    unknown = (set(matched) | set(missing)) - expected
    if unknown:
        raise ModelRetry(f"判卷引用了不存在的评分点：{sorted(unknown)}")
    overlap = set(matched) & set(missing)
    if overlap:
        raise ModelRetry(f"同一评分点不能同时命中和缺失：{sorted(overlap)}")
    uncovered = expected - set(matched) - set(missing)
    if uncovered:
        raise ModelRetry(f"判卷必须覆盖全部评分点，尚未判断：{sorted(uncovered)}")
    if set(matched) == expected:
        if missing or verdict.diagnosis != "complete":
            raise ModelRetry("全部评分点命中时必须没有缺失，且 diagnosis=complete")
    elif verdict.diagnosis == "complete":
        raise ModelRetry("存在缺失评分点时 diagnosis 不能是 complete")
    model_verdict = verdict.verdict
    derived_verdict = derive_verdict(
        expected_point_ids=expected_point_ids,
        matched_point_ids=set(matched),
        critical_point_ids=critical_point_ids,
    )
    return verdict.model_copy(update={"model_verdict": model_verdict, "verdict": derived_verdict})
