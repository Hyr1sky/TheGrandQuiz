"""出题工具——照 Reader 的 LLM 槽模式，为一个 KnowledgeItem 产出 grounded 题（缝 3）。

ADR-0004 的两个 LLM 槽之一（另一个是判卷）：出题走 **role=enrich**（qwen），只产
``{question, cited_evidence}``——它不碰记账（``weak_item_id`` 由判卷后的代码算，见 grading）。

三条设计约束（与 reader 同源）：

- **结构化输出契约（缝 3）**：provider 返回文本经 JSON 解析 + ``GeneratedQuestion`` pydantic
  校验；失败触发 ``ModelRetry``（有界重试，错误反馈进下一次上下文），用尽 → ``QuestionError``。
- **出题校验门（缝 3，eval case 3）——防幽灵题**：``cited_evidence`` 非空，且其中**每条引文都必须
  锚定被考 item 的某条 ``evidence.quote``**（是其子串即可，见 ``ungrounded_citations``——放行
  "Reader 抽长段落、出题引其中短句"）。题必须锚定真实存在的 item 且引真实证据，
  不满足 → ``ModelRetry``。这是运行时的门，不只是 eval 断言。
- **事件上同一条脊柱**：照 ``runner.run_turn`` / ``Reader`` 的模式，每次调用 provider 发
  ``MODEL_STARTED`` →（``payload`` 含 messages 与 prompt_version）→ ``await provider.complete`` →
  ``MODEL_ENDED``。多次重试 = 多个 model span，都挂在 assessment span 下。provider 传输异常
  照 reader 模式：先发 ``MODEL_ENDED(ok=False)`` 闭合 span，再原样冒泡（不吞成 ``QuestionError``，
  以免把 ``ReplayMiss`` 等 harness 错误静默掩盖）。
"""

import json
import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel, Field, ValidationError, model_validator

from grandquiz.domain.learning.difficulty import (
    DistractorQualityPolicy,
    distractor_meets_floor,
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.judge import DistractorLabel, judge_distractor
from grandquiz.domain.learning.models import (
    CitedEvidence,
    KnowledgeItem,
    NonEmptyStr,
    ungrounded_citations,
)
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.kernel.recovery import ErrorClass
from grandquiz.providers.base import Completion, Message, Provider


def _stable_error_summary(exc: ValidationError) -> str:
    """把 pydantic 校验错误压成**版本无关**摘要（只取 loc + type）——理由同 reader。

    ``str(exc)`` 含 pydantic 带版本的 url，会经 retry_note 进下一次 prompt、被 hash 进 replay_key，
    让回放随 pydantic 版本漂移、毁掉逐字节回放（determinism 缝）。故只取稳定的 loc/type。
    """
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in exc.errors())


def dedup_key(text: str) -> str:
    """把一道题归一化成**去重 key**：NFKC + 去空白 / 标点 + 转小写后的裸文本（缝 2 纯函数）。

    无重复出题门（缝 3）与会话内"已问过"台账都以此为相等判据。归一化吸收**表面差异**：
    NFKC 把全 / 半角字母数字标点折叠一致；去所有空白（含中英文空格 / 制表 / 换行）与标点
    （Unicode 类别以 ``P`` 开头）；``lower`` 抹平大小写。故"什么是闭包？"与" 什么是闭包 ?"
    同 key、判为重复；换角度的题归一化后仍不同、放行——只挡逐字 / 近逐字重问同一道题
    （语义近重复的判定属 Tier 2 LLM-judge，不在此确定性门内）。
    """
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        ch.lower()
        for ch in normalized
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


def is_duplicate(text: str, asked_before: Sequence[str]) -> bool:
    """``text`` 归一化后命中 ``asked_before`` 里任一已问过的题 → True（缝 2 命中判定）。

    空台账（首次出题、或不传台账的调用方）恒返回 False——去重是纯附加，不影响首题 / 既有调用方。
    """
    key = dedup_key(text)
    return any(dedup_key(prev) == key for prev in asked_before)


# meta 选项（反-tell 门 a）：泄漏题型的非真干扰项，prompt 已硬禁；这里兜底 egregious 泄漏。
# 中文 meta 几乎都以指代性前缀起头（以上 / 上述 / 综上），英文用 all/none of the above。
# 只认这些锚定形态、不做 bare 子串匹配——否则 "都对" 误伤 "两者都对齐"、"都不对" 误伤
# "指针都不对齐边界"。均在 ``dedup_key`` 归一化（吸收大小写 / 全半角 / 标点）后判定。
_META_PREFIXES_ZH: tuple[str, ...] = ("以上", "上述", "综上")
_META_SUBSTRINGS_EN: tuple[str, ...] = ("alloftheabove", "noneoftheabove")


def has_meta_option(options: Sequence[str]) -> bool:
    """任一选项是 meta 选项 → True（反-tell 门 a，缝 2 纯函数）。

    中文按指代性前缀（以上 / 上述 / 综上）匹配、英文按 "all/none of the above" 子串匹配，均在
    ``dedup_key`` 归一化后判定。刻意不做 bare 短语（"都对"）子串匹配以免误伤合法选项；对既有假选项
    （"正确选项 / 干扰项 / 值的快照 / 变量本身 / a value snapshot / the variable itself"）无一命中。
    """
    zh_prefixes = [dedup_key(p) for p in _META_PREFIXES_ZH]
    for opt in options:
        key = dedup_key(opt)
        if any(prefix and key.startswith(prefix) for prefix in zh_prefixes):
            return True
        if any(sub in key for sub in _META_SUBSTRINGS_EN):
            return True
    return False


def has_length_outlier(options: Sequence[str], answer_index: int) -> bool:
    """正确项长度远超所有干扰项（> 最长干扰项的 2 倍）→ True（反-tell 门 b，缝 2）。

    "正确项独长"是最廉价的表面 tell（模型忍不住把正解写全、干扰项敷衍）。按字符数（``len``，CJK 记
    1）比较，阈值刻意保守（2×）只抓 egregious，平行选项恒放行。**只查"独长"一个方向**：正确项独短
    往往是合法形态（正解是单一术语、干扰项是完整错误短语，如 ["变量", "外层的作用域链", "闭包捕获
    环境"]），不作 tell 处理以免误伤。少于 2 项 / 无干扰项 / answer_index 越界 → False（交由既有
    可判卷门处理，本门不与其抢先报错）。
    """
    if not 0 <= answer_index < len(options):
        return False
    distractor_lens = [len(opt) for i, opt in enumerate(options) if i != answer_index]
    if not distractor_lens:
        return False
    return len(options[answer_index]) > 2 * max(distractor_lens)


class QuestionError(Exception):
    """出题失败——有界重试用尽仍拿不到合法、锚定真实证据的 ``GeneratedQuestion``。

    ``assess_once`` 视其为基础设施级失败、闭合 assessment span 后原样冒泡（不掩盖）；
    ``error_class = DEGRADED`` 标示"本轮可恢复"——由 kernel ``RecoveryPolicy`` 统一裁决为跳过本轮。
    """

    error_class = ErrorClass.DEGRADED


class ModelRetry(Exception):
    """结构化输出 / 校验门失败的重试信号（借 pydantic-ai 语义，同 reader）。

    被本模块的有界重试循环捕获——把校验错误反馈进下一次调用的上下文；重试预算耗尽则升级为
    ``QuestionError``。它是"输出可验证"的运行时门，不冒泡给调用方。
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "invalid_output",
        retained_options: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retained_options = tuple(retained_options)


class ExpectedPoint(BaseModel):
    """一道开放题的可审计评分点；每个点必须绑定该题引用的一条原文证据。

    ``required_claims`` 是该点内部固定 ``all-of`` 的原子接受条件。旧题没有该字段时，
    ``grading_claims`` 退回单条 ``description``，使历史 Snapshot 继续可读、可回放。
    """

    point_id: NonEmptyStr
    description: NonEmptyStr
    required_claims: list[NonEmptyStr] = Field(
        default_factory=list,
        max_length=3,
        exclude_if=lambda value: not value,
    )
    cited_evidence: NonEmptyStr

    @model_validator(mode="after")
    def _required_claims_are_unique(self) -> "ExpectedPoint":
        if len(self.required_claims) != len(set(self.required_claims)):
            raise ValueError("required_claims 不得重复")
        return self

    @property
    def grading_claims(self) -> tuple[str, ...]:
        """Return the explicit all-of claims, or the legacy single-description contract."""

        return tuple(self.required_claims or [self.description])


class QuestionSpec(BaseModel):
    """开放题的唯一题目规格：题干、评分点、参考作答与原文证据。

    ``question`` 非空（``NonEmptyStr``，strip 后为空也拒）；``cited_evidence`` 的非空与"锚定
    被考 item 证据（子串即可）"由 ``generate_question`` 的校验门把关。``expected_points`` 是判卷
    的唯一 rubric；``critical_point_ids`` 在出题时预注册缺失即足以判错的核心点；
    ``reference_answer`` 回答的必须是本题，而不是泛化复述整个 KnowledgeItem。
    刻意不产 ``item_id`` / ``weak_item_id``——出题不记账，被考 item 由调用方指定、记账由判卷后的
    代码算（ADR-0004）。
    """

    question: NonEmptyStr
    expected_points: list[ExpectedPoint] = Field(min_length=1)
    critical_point_ids: list[NonEmptyStr] = Field(default_factory=list)
    reference_answer: NonEmptyStr
    cited_evidence: CitedEvidence

    @model_validator(mode="after")
    def _critical_points_belong_to_the_rubric(self) -> "QuestionSpec":
        claim_modes = [bool(point.required_claims) for point in self.expected_points]
        if any(claim_modes) and not all(claim_modes):
            raise ValueError(
                "同一 QuestionSpec 的 expected_points 必须全部提供 required_claims，"
                "或全部保持旧版 description 契约"
            )
        critical = list(self.critical_point_ids)
        if len(critical) != len(set(critical)):
            raise ValueError("critical_point_ids 不得重复")
        expected = {point.point_id for point in self.expected_points}
        unknown = set(critical) - expected
        if unknown:
            raise ValueError(f"critical_point_ids 必须引用已有评分点：{sorted(unknown)}")
        return self


# 兼容既有导入名；领域内的新权威术语是 QuestionSpec。
GeneratedQuestion = QuestionSpec


class MultipleChoiceQuestion(BaseModel):
    """选择题 LLM 的结构化输出契约：题干 + 选项 + 正确项下标 + 锚定的原文证据引文。

    ``answer_index`` 是正确项在 ``options`` 里的下标——判卷走**确定性代码**（responder 所选项文本
    == ``options[answer_index]`` → 对 / 错），**不调 LLM**（PRD："选择题确定性比对"）。合法性
    （``options`` ≥ 2、``answer_index`` 合法、``cited_evidence`` 非空且子串锚定被考 item 证据）
    由 ``generate_multiple_choice`` 的校验门把关（防幽灵题 + 防不可判卷）。与出题同源：刻意不产
    ``item_id`` / ``weak_item_id``——出题不记账（ADR-0004）。
    """

    question: NonEmptyStr
    options: list[NonEmptyStr]  # 非空（strip 后也非空）；两两可区分由 _parse_mc 的门把关
    answer_index: int
    cited_evidence: CitedEvidence


async def generate_question(
    item: KnowledgeItem,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    max_attempts: int = 3,
    prompt_name: str = "question_generate",
    language: str = "中文",
    asked_before: Sequence[str] = (),
    difficulty_hint: str | None = None,
) -> QuestionSpec:
    """为 ``item`` 产出一道 grounded 题；持续失败 → ``QuestionError``。见模块 docstring。

    ``max_attempts``：1 次初始调用 + 最多 ``max_attempts - 1`` 次重试（默认 3；测试可收紧）。
    ``prompt_name``：出题 system prompt 模板名——默认 ``question_generate``（标准开放题）；
    追问深挖传 ``question_probe``（同一 schema、仅换 prompt 逼深一层）。trace 记的 prompt_version
    随之反映所用变体，故 eval 回归可归因到具体题型 prompt（追问用例即靠此断言走了 probe）。
    ``language``：出题语言（默认"中文"，由 ``assess_once`` 解析偏好 > 中文后下传；语言来自
    Preference Memory，而非临时考核输入）——
    用字面 ``str.replace`` 把模板里的 ``{{LANGUAGE}}`` 哨兵换成它（**不用 str.format**：模板含
    JSON schema 示例的字面花括号，format 会崩）。模板文件内容（含字面 ``{{LANGUAGE}}``）才是
    prompt 版本号的哈希对象，故版本号跨语言稳定；只有发出的 message 及 replay_key 按语言不同。
    ``asked_before``：本会话内**已问过**该 item 的题目文本（会话内"已问过"台账，由 ``assess_once``
    从 ``recently_asked`` 取被考 item 的已问列表下传，"LLM 判卷，代码记账"）。**仅当非空时**往 user
    message 注入"请换角度、勿重复"的约束（为空时发出的 message 一字不改——保证首次出题及不传台账的
    调用方 message / replay_key / prompt 版本不变），并在 ``_parse`` 的归一化去重门用它做重复判定。
    ``difficulty_hint``：**开放 / 追问难度软杠杆**（SE-S6）——按被考 item 难度档算出的难度提示文本，
    由 ``assess_once`` 读难度台账经 ``difficulty.difficulty_prompt_hint`` 算出并下传（高档逼边界 /
    反例 / 跨概念，低档问核心定义；默认档 / 未接台账 → None）。**仅当非 None 时**才追加一条难度约束
    user message（照 ``asked_before`` / ``num_options`` 的"可选追加、None 时一字不改"先例，见
    ``_append_difficulty_hint``）；**``None`` 时（默认路径 / 既有调用方 / eval harness）不追加任何
    message**——发出的 message / replay_key / prompt 版本号与改动前**逐字节相同**（eval / cassette
    字节等价的命根）。开放题与追问共用本入口，两者都经此追加（``prompt_name`` 只决定加载哪个模板）。
    **软性如实标注**：这条比 MC 硬杠杆软——只保证不同档追加不同提示文本，**不保证也不断言"高档题真的
    更难"**（深度是主观的、超出确定性可断言范围，见 ``difficulty.difficulty_prompt_hint``）。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    prompt = load_prompt(prompt_name)
    valid_quotes = {ev.quote for ev in item.evidence}
    evidence_block = "\n".join(f"- {ev.quote}" for ev in item.evidence)
    base_messages = [
        Message(role="system", content=prompt.text.replace("{{LANGUAGE}}", language)),
        Message(
            role="user",
            content=(
                "被考知识点：\n"
                f"概念：{item.concept}\n"
                f"摘要：{item.summary}\n"
                "可引用的原文证据（cited_evidence 只能逐字从中挑选）：\n"
                f"{evidence_block}"
            ),
        ),
    ]
    _append_asked_before(base_messages, asked_before)
    # 顺序固定（先 asked_before 再 difficulty_hint）保证可复现：难度提示恒在"换角度"约束之后追加。
    _append_difficulty_hint(base_messages, difficulty_hint)
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
            return _parse(completion.text, valid_quotes, asked_before=asked_before)
        except ModelRetry as exc:
            last_error = str(exc)
            retry_note = f"上一次出题无法采用：{exc}。请只返回合法 JSON，且引用真实证据。"
    raise QuestionError(f"出题失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")


def _append_asked_before(messages: list[Message], asked_before: Sequence[str]) -> None:
    """已问过台账非空时，往 user message 追加"请换角度、勿重复"的约束（为空则一字不改）。

    出题与选择题出题共用（两处 ``base_messages`` 组装同一约束）。空台账时不追加任何 message——
    保证首次出题及不传台账的调用方发出的 message / replay_key / prompt 版本号完全不变。
    """
    if not asked_before:
        return
    asked_block = "\n".join(f"- {q}" for q in asked_before)
    messages.append(
        Message(
            role="user",
            content=(f"已问过以下问题，请换一个角度提问、不要重复：\n{asked_block}"),
        )
    )


def _append_difficulty_hint(messages: list[Message], difficulty_hint: str | None) -> None:
    """难度提示非 None 时，往 user message 追加一条难度约束（内容即 hint）；None 则一字不改。

    SE-S6 开放 / 追问软杠杆，照 ``_append_asked_before`` 的"可选追加、None 时 message 逐字节不变"
    先例：仅 ``difficulty_hint is not None`` 时追加，内容就是调用方（``assess_once`` 经
    ``difficulty.difficulty_prompt_hint``）按难度档算好的整句提示文本；``difficulty_hint is None``
    （默认路径 / 既有调用方 / eval harness）时**不追加任何 message**——保证发出的 message /
    replay_key / prompt 版本号与改动前逐字节相同（cassette / replay 不破的命根）。开放题与追问共用
    本函数（``generate_question`` 是两者共用入口）。
    """
    if difficulty_hint is None:
        return
    messages.append(Message(role="user", content=difficulty_hint))


async def _call_model(
    messages: list[Message],
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    prompt_version: str,
) -> Completion:
    # 照 reader._call_model：一对 MODEL_STARTED / MODEL_ENDED 共享 span_id；出题走 role=enrich。
    span_id = emitter.new_span_id()
    emitter.emit(
        EventType.MODEL_STARTED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload={
            "messages": [m.model_dump() for m in messages],
            "prompt_version": prompt_version,
            "role": "enrich",
        },
    )
    try:
        completion = await provider.complete(messages, role="enrich")
    except Exception as exc:
        # provider 传输异常 / ReplayMiss：先闭合 span（started/ended 配对不变量），再原样冒泡。
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=span_id,
            parent_span_id=parent_span_id,
            payload={"ok": False, "error": repr(exc)},
        )
        raise
    emitter.emit(
        EventType.MODEL_ENDED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload={"ok": True, "output": completion.text, "usage": completion.usage.model_dump()},
    )
    return completion


def _parse(text: str, valid_quotes: set[str], asked_before: Sequence[str] = ()) -> QuestionSpec:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        question = QuestionSpec.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc
    # 出题校验门（缝 3，eval case 3）：非空 + 每条引文都锚定被考 item 的真实证据（防幽灵题）。
    # 锚定 = 引文是某条 evidence.quote 的子串（见 ungrounded_citations）——放行"抽长段落、引短句"。
    if not question.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：题必须引用被考知识点的原文证据")
    ghost = ungrounded_citations(question.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
    point_ids = [point.point_id for point in question.expected_points]
    if len(point_ids) != len(set(point_ids)):
        raise ModelRetry("expected_points.point_id 必须在单题内唯一")
    point_quotes = [point.cited_evidence for point in question.expected_points]
    ghost_points = ungrounded_citations(point_quotes, valid_quotes)
    if ghost_points:
        raise ModelRetry(f"评分点引用了不属于被考知识点的证据（幽灵引文）：{ghost_points}")
    missing_from_question = [
        quote for quote in point_quotes if quote not in question.cited_evidence
    ]
    if missing_from_question:
        raise ModelRetry(
            "每个评分点的 cited_evidence 必须同时出现在题目的 cited_evidence 中："
            f"{missing_from_question}"
        )
    # 归一化去重门（缝 3）：新题归一化后命中会话内"已问过"台账 → ModelRetry（复用有界重试）。
    # 即使 LLM 无视 user message 里的"换角度"约束，这道确定性门也保证重复题不会到达学习者。
    if is_duplicate(question.question, asked_before):
        raise ModelRetry("与已问过的题重复：请换一个角度提问，不要重复已考过的问题")
    return question


async def generate_multiple_choice(
    item: KnowledgeItem,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    max_attempts: int = 3,
    language: str = "中文",
    asked_before: Sequence[str] = (),
    num_options: int | None = None,
    quality_policy: DistractorQualityPolicy | None = None,
) -> MultipleChoiceQuestion:
    """为 ``item`` 产一道锚定的选择题（首次接触概念的热身题型）；持续失败 → ``QuestionError``。

    与 ``generate_question`` 同源（role=enrich、结构化输出契约、事件上脊柱、有界重试），仅多两条
    MC 专属校验门（缝 3）：

    - **可判卷门**：``options`` 至少 2 项、``answer_index`` 是 ``options`` 的合法下标——否则确定性
      判卷（``grade_multiple_choice``）无从比对，出题即不合格。
    - **防幽灵题门**：``cited_evidence`` 非空且每条锚定被考 item 证据（子串即可，与开放题同规则）。

    任一不满足 → ``ModelRetry``（反馈进下一次上下文）；重试预算耗尽 → ``QuestionError``。
    provider 传输异常照 ``_call_model`` 模式先闭合 model span 后原样冒泡（不吞）。
    ``language`` 同 ``generate_question``：字面替换模板 ``{{LANGUAGE}}`` 哨兵，版本号跨语言稳定。
    ``asked_before`` 同 ``generate_question``：非空时注入"换角度"约束、并在 ``_parse_mc`` 归一化
    去重门用作重复判定（为空则 message 一字不改）——会话内不重复出同一道选择题。
    ``num_options`` 是确定性的目标选项数；传入时追加约束并严格校验，未传时保持默认出题路径。
    ``quality_policy`` 是高档题的**集合质量契约**，例如“每项至少较弱，其中至少两项合理”。它不再
    要求所有干扰项都达到最高档，也不靠 5/6 个选项堆出表面难度。首次产出后会评审每个干扰项；未
    达标时冻结题干、正确项、证据与已通过项，只让模型替换必要的坏项。相同选项的 judge 结果在一次
    生成任务内复用，避免每轮全量重评。耗尽有界预算后抛 ``QuestionError``，由接口层恢复策略决定
    重试或跳过，不把整个考核误判为不可恢复失败。
    整个过程用一个 question-generation span 包住 model 与 judge 子 span，并记录拒绝原因、修复次数
    和 judge 调用数，便于区分“生成慢”“质量门拒绝”和“局部修复失败”。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    effective_policy = quality_policy
    prompt = load_prompt("question_multiple_choice")
    valid_quotes = {ev.quote for ev in item.evidence}
    evidence_block = "\n".join(f"- {ev.quote}" for ev in item.evidence)
    base_messages = [
        Message(role="system", content=prompt.text.replace("{{LANGUAGE}}", language)),
        Message(
            role="user",
            content=(
                "被考知识点：\n"
                f"概念：{item.concept}\n"
                f"摘要：{item.summary}\n"
                "可引用的原文证据（cited_evidence 只能逐字从中挑选）：\n"
                f"{evidence_block}"
            ),
        ),
    ]
    _append_asked_before(base_messages, asked_before)
    _append_num_options(base_messages, num_options)
    generation_span = emitter.new_span_id()
    emitter.emit(
        LearningEvent.MULTIPLE_CHOICE_GENERATION_STARTED,
        span_id=generation_span,
        parent_span_id=parent_span_id,
        payload={
            "status": "running",
            "item_id": item.item_id,
            "question_type": "选择题",
            "target_option_count": num_options,
            "max_attempts": max_attempts,
            "quality_policy": (
                None
                if effective_policy is None
                else {
                    "minimum_label": effective_policy.minimum_label,
                    "minimum_reasonable": effective_policy.minimum_reasonable,
                }
            ),
        },
    )
    retry_note: str | None = None
    last_error = ""
    last_reason_code = "invalid_output"
    anchor: MultipleChoiceQuestion | None = None
    retained_options: tuple[str, ...] = ()
    verdict_cache: dict[str, DistractorLabel] = {}
    attempts = 0
    repair_attempts = 0
    failure_stage = "model_call"
    try:
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            is_repair = anchor is not None
            if is_repair:
                repair_attempts += 1
            messages = list(base_messages)
            if retry_note is not None:
                messages.append(Message(role="user", content=retry_note))
            failure_stage = "model_call"
            completion = await _call_model(
                messages,
                provider=provider,
                emitter=emitter,
                parent_span_id=generation_span,
                prompt_version=prompt.version,
            )
            failure_stage = "validation"
            mc: MultipleChoiceQuestion | None = None
            try:
                mc = _parse_mc(
                    completion.text,
                    valid_quotes,
                    asked_before=asked_before,
                    num_options=num_options,
                )
                if anchor is not None:
                    failure_stage = "repair_validation"
                    _validate_mc_repair(anchor, mc, retained_options)
                if effective_policy is not None:
                    failure_stage = "distractor_quality"
                    retained_options = await _assess_distractor_policy(
                        mc,
                        item,
                        effective_policy,
                        verdict_cache=verdict_cache,
                        provider=provider,
                        emitter=emitter,
                        parent_span_id=generation_span,
                    )
                emitter.emit(
                    LearningEvent.MULTIPLE_CHOICE_GENERATION_ENDED,
                    span_id=generation_span,
                    parent_span_id=parent_span_id,
                    payload={
                        "ok": True,
                        "attempts": attempts,
                        "repair_attempts": repair_attempts,
                        "judge_calls": len(verdict_cache),
                    },
                )
                return mc
            except ModelRetry as exc:
                last_error = str(exc)
                last_reason_code = exc.reason_code
                if exc.reason_code == "distractor_quality_unmet" and mc is not None:
                    retained_options = exc.retained_options
                    anchor = mc
                emitter.emit(
                    LearningEvent.MULTIPLE_CHOICE_GENERATION_ATTEMPT_REJECTED,
                    parent_span_id=generation_span,
                    payload={
                        "attempt": attempt,
                        "stage": "repair" if is_repair else "generation",
                        "reason_code": exc.reason_code,
                        "retained_distractor_count": len(retained_options),
                    },
                )
                retry_note = _multiple_choice_retry_note(
                    exc,
                    anchor=anchor,
                    retained_options=retained_options,
                )
        raise QuestionError(f"选择题出题失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")
    except Exception as exc:
        failure_payload: dict[str, object] = {
            "ok": False,
            "attempts": attempts,
            "repair_attempts": repair_attempts,
            "judge_calls": len(verdict_cache),
            "stage": failure_stage,
        }
        if isinstance(exc, QuestionError):
            failure_payload["reason_code"] = last_reason_code
        else:
            failure_payload["error_type"] = type(exc).__name__
        emitter.emit(
            LearningEvent.MULTIPLE_CHOICE_GENERATION_ENDED,
            span_id=generation_span,
            parent_span_id=parent_span_id,
            payload=failure_payload,
        )
        raise


async def _assess_distractor_policy(
    mc: MultipleChoiceQuestion,
    item: KnowledgeItem,
    policy: DistractorQualityPolicy,
    *,
    verdict_cache: dict[str, DistractorLabel],
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
) -> tuple[str, ...]:
    """评完整组干扰项；达标返回，未达标只要求替换必要项并保留其余项。"""
    correct_answer = mc.options[mc.answer_index]
    distractors = [option for index, option in enumerate(mc.options) if index != mc.answer_index]
    labels: dict[str, DistractorLabel] = {}
    for option in distractors:
        label = verdict_cache.get(option)
        if label is None:
            verdict = await judge_distractor(
                item,
                question=mc.question,
                correct_answer=correct_answer,
                distractor=option,
                provider=provider,
                emitter=emitter,
                parent_span_id=parent_span_id,
            )
            label = verdict.label
            verdict_cache[option] = label
        labels[option] = label

    replacements = {
        option
        for option, label in labels.items()
        if not distractor_meets_floor(label, policy.minimum_label)
    }
    reasonable_count = sum(label == "合理干扰" for label in labels.values())
    reasonable_shortfall = max(0, policy.minimum_reasonable - reasonable_count)
    if reasonable_shortfall:
        weak_candidates = [
            option
            for option in distractors
            if labels[option] == "较弱干扰" and option not in replacements
        ]
        replacements.update(weak_candidates[:reasonable_shortfall])

    if not replacements:
        return tuple(distractors)
    retained = tuple(option for option in distractors if option not in replacements)
    rejected = "、".join(f"「{option}」({labels[option]})" for option in replacements)
    raise ModelRetry(
        "干扰项集合未达到质量策略："
        f"需每项至少「{policy.minimum_label}」且至少 {policy.minimum_reasonable} 项为「合理干扰」；"
        f"请只替换 {rejected}",
        reason_code="distractor_quality_unmet",
        retained_options=retained,
    )


def _validate_mc_repair(
    anchor: MultipleChoiceQuestion,
    candidate: MultipleChoiceQuestion,
    retained_options: Sequence[str],
) -> None:
    """局部修复不得改写已经通过的题干、答案、证据与干扰项。"""
    invariant_changed = (
        candidate.question != anchor.question
        or candidate.answer_index != anchor.answer_index
        or candidate.options[candidate.answer_index] != anchor.options[anchor.answer_index]
        or list(candidate.cited_evidence) != list(anchor.cited_evidence)
    )
    missing_retained = [option for option in retained_options if option not in candidate.options]
    if invariant_changed or missing_retained:
        raise ModelRetry(
            "局部修复改写了题干、正确项、证据或已通过干扰项；只能替换被拒绝的干扰项",
            reason_code="repair_contract_violated",
            retained_options=retained_options,
        )


def _multiple_choice_retry_note(
    exc: ModelRetry,
    *,
    anchor: MultipleChoiceQuestion | None,
    retained_options: Sequence[str],
) -> str:
    if anchor is None:
        return f"上一次出题无法采用：{exc}。请只返回合法 JSON，且引用真实证据。"
    frozen = {
        "question": anchor.question,
        "correct_answer": anchor.options[anchor.answer_index],
        "answer_index": anchor.answer_index,
        "cited_evidence": list(anchor.cited_evidence),
        "retained_distractors": list(retained_options),
    }
    return (
        f"上一次选择题的干扰项需要局部修复：{exc}。"
        "请返回完整合法 JSON，但必须原样保留下列题干、正确项、answer_index、证据和已通过干扰项；"
        "只替换未通过的干扰项，不得扩写摘要中没有被 cited_evidence 直接支持的事实。\n"
        f"冻结内容：{json.dumps(frozen, ensure_ascii=False, sort_keys=True)}"
    )


def _append_num_options(messages: list[Message], num_options: int | None) -> None:
    """按难度档要求恰好给出 ``num_options`` 个选项时，往 user message 追加选项数约束（None 不改）。

    照 ``_append_asked_before`` 的"可选追加、为空时 message 逐字节不变"先例（SE-S5a 选择题硬杠杆
    ①）：仅 ``num_options is not None`` 时追加一条约束，指明目标选项数 + 干扰项质量 / 平行度要求；
    ``num_options is None``（默认路径、既有调用方 / eval harness）时**不追加任何 message**——保证
    发出的 message / replay_key / prompt 版本号与改动前逐字节相同。
    """
    if num_options is None:
        return
    messages.append(
        Message(
            role="user",
            content=(
                f"本题请恰好给出 {num_options} 个选项："
                f"1 个正确项 + {num_options - 1} 个有迷惑性、需真懂概念才能排除的干扰项；"
                "选项之间在长度 / 具体度上保持平行，不要让正确项被表面特征出卖。"
                "摘要只用于帮助理解概念；正确答案的实质主张必须由 cited_evidence 直接支持。"
                "若 Evidence 只支持较窄主张，就缩小问题，不得把摘要额外细节伪装成原文事实。"
            ),
        )
    )


def _parse_mc(
    text: str,
    valid_quotes: set[str],
    asked_before: Sequence[str] = (),
    num_options: int | None = None,
) -> MultipleChoiceQuestion:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}", reason_code="invalid_json") from exc
    try:
        mc = MultipleChoiceQuestion.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(
            f"输出不符合 schema：{_stable_error_summary(exc)}",
            reason_code="schema_invalid",
        ) from exc
    # 可判卷门：选项 ≥ 2、正确项下标合法——否则确定性判卷无从比对。
    if len(mc.options) < 2:
        raise ModelRetry(
            f"选项至少需 2 项（现 {len(mc.options)} 项）：选择题需可区分的干扰项",
            reason_code="option_count_invalid",
        )
    if not 0 <= mc.answer_index < len(mc.options):
        raise ModelRetry(
            f"answer_index 越界：{mc.answer_index} 不在合法下标 [0, {len(mc.options)}) 内",
            reason_code="answer_index_invalid",
        )
    # 选项须两两可区分：确定性判卷按文本比对，重复选项会让"选了干扰项"被误判为对、污染薄弱账本。
    # （空 / 纯空白选项已由 options 的 NonEmptyStr 挡下。）
    if len(set(mc.options)) != len(mc.options):
        raise ModelRetry(
            f"选项含重复文本：{mc.options}——确定性判卷按文本比对，选项须两两可区分",
            reason_code="duplicate_options",
        )
    # 反-tell 门（缝 3）：只挡表面泄漏、不测 plausibility——干扰项 plausibility（"不懂概念能否排除"）
    # 的真打分是 Tier 2 LLM-judge，显式不在本 issue。放在可判卷门之后（options ≥ 2、answer_index
    # 合法已保证），故可安全区分正确项 / 干扰项。prompt 已把这些从软约束升为硬约束，这两道确定性门
    # 只兜底 egregious 泄漏，阈值保守到不误伤既有平衡假选项。
    # (a) meta 选项禁令（"以上都对 / 都不对 / all of the above" 等）。
    if has_meta_option(mc.options):
        raise ModelRetry(
            "含 meta 选项（如'以上都对 / 都不对 / all of the above'）：每个干扰项须是具体的常见"
            "误解或邻近但错的概念，不得用 meta 选项泄漏题型",
            reason_code="meta_option",
        )
    # (b) 长度离群：正确项 > 最长干扰项 2 倍、或 < 最短干扰项一半（答案被长度出卖）。
    if has_length_outlier(mc.options, mc.answer_index):
        raise ModelRetry(
            "正确项与干扰项长度悬殊（答案被长度出卖）：请让所有选项在长度 / 具体度 / 语法上平行",
            reason_code="length_outlier",
        )
    # 刻意不加"题干回声"（stem-echo）门：中文无可靠分词，按字 / n-gram 匹配误报率高（正解与题干
    # 天然共享领域词），易误伤合法题；题干回声只在 prompt 里硬禁，其检测留给 Tier 2 judge。
    # 防幽灵题门（与开放题同规则）：cited_evidence 非空 + 每条都锚定被考 item 真实证据（子串即可）。
    if not mc.cited_evidence:
        raise ModelRetry(
            "cited_evidence 不能为空：题必须引用被考知识点的原文证据",
            reason_code="evidence_missing",
        )
    ghost = ungrounded_citations(mc.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(
            f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}",
            reason_code="ghost_evidence",
        )
    # 归一化去重门（缝 3，与开放题同规则）：题干归一化后命中会话内"已问过"台账 → ModelRetry。
    if is_duplicate(mc.question, asked_before):
        raise ModelRetry(
            "与已问过的题重复：请换一个角度提问，不要重复已考过的问题",
            reason_code="question_repeated",
        )
    # 选项数硬杠杆门（SE-S5a，缝 3，**仅当调用方按难度档传入 num_options 时生效**）：档越高、要求
    # 选项越多、越难靠排除法蒙对。少于目标数 → ModelRetry，反馈进下一次上下文让重试补足干扰项。
    # 放在所有既有门之后（不与既有 >= 2 / answer_index / 去重 / meta / 长度门抢先报错）；
    # num_options is None（默认路径 / 既有调用方 / eval harness）时**此门整个不参与**——行为与
    # 改动前逐字节等价。
    if num_options is not None and len(mc.options) != num_options:
        raise ModelRetry(
            f"选项数不符：本题要求恰好 {num_options} 个选项（现 {len(mc.options)} 项），"
            "请只返回指定数量且有迷惑性、需真懂概念才能排除的选项",
            reason_code="option_count_unmet",
        )
    return mc
