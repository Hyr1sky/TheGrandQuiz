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

from pydantic import BaseModel, ValidationError

from grandquiz.domain.learning.difficulty import distractor_meets_floor
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


class GeneratedQuestion(BaseModel):
    """出题 LLM 的结构化输出契约：一道题 + 其锚定的原文证据引文。

    ``question`` 非空（``NonEmptyStr``，strip 后为空也拒）；``cited_evidence`` 的非空与"锚定
    被考 item 证据（子串即可）"由 ``generate_question`` 的校验门把关。刻意不产 ``item_id`` /
    ``weak_item_id``——出题不记账，被考 item 由调用方指定、记账由判卷后的代码算（ADR-0004）。
    """

    question: NonEmptyStr
    cited_evidence: CitedEvidence


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
) -> GeneratedQuestion:
    """为 ``item`` 产出一道 grounded 题；持续失败 → ``QuestionError``。见模块 docstring。

    ``max_attempts``：1 次初始调用 + 最多 ``max_attempts - 1`` 次重试（默认 3；测试可收紧）。
    ``prompt_name``：出题 system prompt 模板名——默认 ``question_generate``（标准开放题）；
    追问深挖传 ``question_probe``（同一 schema、仅换 prompt 逼深一层）。trace 记的 prompt_version
    随之反映所用变体，故 eval 回归可归因到具体题型 prompt（追问用例即靠此断言走了 probe）。
    ``language``：出题语言（默认"中文"，由 ``assess_once`` 解析偏好 > 中文后下传；ADR-0005 消解
    ``LearningTask`` 后语言来自 Preference Memory 而非 task）——
    用字面 ``str.replace`` 把模板里的 ``{{LANGUAGE}}`` 哨兵换成它（**不用 str.format**：模板含
    JSON schema 示例的字面花括号，format 会崩）。模板文件内容（含字面 ``{{LANGUAGE}}``）才是
    prompt 版本号的哈希对象，故版本号跨语言稳定；只有发出的 message 及 replay_key 按语言不同。
    ``asked_before``：本会话内**已问过**该 item 的题目文本（会话内"已问过"台账，由 ``assess_once``
    从 ``recently_asked`` 取被考 item 的已问列表下传，"LLM 判卷，代码记账"）。**仅当非空时**往 user
    message 注入"请换角度、勿重复"的约束（为空时发出的 message 一字不改——保证首次出题及不传台账的
    调用方 message / replay_key / prompt 版本不变），并在 ``_parse`` 的归一化去重门用它做重复判定。
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


def _parse(
    text: str, valid_quotes: set[str], asked_before: Sequence[str] = ()
) -> GeneratedQuestion:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        question = GeneratedQuestion.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc
    # 出题校验门（缝 3，eval case 3）：非空 + 每条引文都锚定被考 item 的真实证据（防幽灵题）。
    # 锚定 = 引文是某条 evidence.quote 的子串（见 ungrounded_citations）——放行"抽长段落、引短句"。
    if not question.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：题必须引用被考知识点的原文证据")
    ghost = ungrounded_citations(question.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
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
    quality_floor: DistractorLabel | None = None,
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
    ``num_options``：**选择题难度硬杠杆①**（SE-S5a）——按被考 item 的难度档算出的目标选项数，由
    ``assess_once`` 读难度台账后下传（档越高、选项越多、越难靠排除法蒙对，见
    ``difficulty.target_option_count``）。**仅当非 None 时**才追加一条选项数约束（照
    ``asked_before`` 的"可选追加、为空一字不改"先例，见 ``_append_num_options``），并在
    ``_parse_mc`` 末尾加一道"至少 ``num_options`` 项"的门。**``None`` 时（默认路径 / 既有调用方 /
    eval harness）不追加任何 message、``_parse_mc`` 行为完全不变**——发出的 message / replay_key /
    prompt 版本号与改动前逐字节相同（eval / cassette 字节等价的命根子）。
    ``quality_floor``：**选择题难度硬杠杆②**（SE-S5b）——按被考 item 难度档要求的**最低可接受干扰项
    质量档**（``DistractorLabel``），由 ``assess_once`` 读难度台账后经 ``distractor_quality_floor``
    算出并下传（仅高档 4/5 非 None，见 ``difficulty.distractor_quality_floor``）。
    **仅当非 None 时**，在 ``_parse_mc`` 拿到合法 MC **之后**、``return`` 之前，对**每个干扰项**
    （``options`` 里除 ``answer_index`` 外的项）调 ``judge_distractor``（Tier-2 判官、role=basic）
    评其 plausibility；
    **任一**干扰项未达 ``quality_floor``（``not distractor_meets_floor``）→ ``ModelRetry``（点名太弱
    的那个、要求换更有迷惑性的干扰项），由既有有界重试循环重新生成；重试预算耗尽仍不达标 →
    ``QuestionError``（同 ``num_options`` 门，交 ``RecoveryPolicy`` DEGRADED 跳过本轮、不炸会话）。
    **``None`` 时（默认路径 / 既有调用方 / eval harness）一次都不调 judge，行为与改动前逐字节等价**
    （judge 一调都不调是 cassette 不破的命根子）——judge 只在升过默认档的概念上触发。judge 的 model
    span 挂在传入的 ``parent_span_id`` 之下（``judge_distractor`` 自负责发 MODEL_STARTED/ENDED），
    故高档题的 trace 里能看到 judge 评了几次、重生成了几次（可观测）。
    **成本护栏**：judge 每题最多评 ``(选项数 - 1) × max_attempts`` 次（每次重生成都要重评全部干扰
    项），且**仅高档（4/5）触发**——默认 / 降档 / 新概念（``quality_floor is None``）零 judge 开销。
    门槛表（``_TIER_QUALITY_FLOOR``）与达标比较（``distractor_meets_floor``）均在 ``difficulty.py``
    集中、可调。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
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
            mc = _parse_mc(
                completion.text,
                valid_quotes,
                asked_before=asked_before,
                num_options=num_options,
            )
            # judge 验收闸门（SE-S5b 杠杆②，**仅当调用方按难度档传入 quality_floor 时生效**）：
            # 拿到合法 MC 后再逐个 judge 干扰项，任一不达标 → ModelRetry 重生成（由本循环重试）。
            # quality_floor is None（默认路径 / 既有调用方 / eval harness）时**整个不参与、judge
            # 一次都不调**——行为与改动前逐字节等价（cassette / replay 不破的命根子）。
            if quality_floor is not None:
                await _enforce_distractor_floor(
                    mc,
                    item,
                    quality_floor,
                    provider=provider,
                    emitter=emitter,
                    parent_span_id=parent_span_id,
                )
            return mc
        except ModelRetry as exc:
            last_error = str(exc)
            retry_note = f"上一次出题无法采用：{exc}。请只返回合法 JSON，且引用真实证据。"
    raise QuestionError(f"选择题出题失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")


async def _enforce_distractor_floor(
    mc: MultipleChoiceQuestion,
    item: KnowledgeItem,
    quality_floor: DistractorLabel,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
) -> None:
    """对 MC 的每个干扰项跑 Tier-2 judge，任一未达 ``quality_floor`` → ``ModelRetry``（点名该项）。

    干扰项 = ``options`` 里除 ``answer_index`` 外的全部项；正确项 = ``options[answer_index]``。
    逐项调 ``judge_distractor``（role=basic，自负责发 model span 挂 ``parent_span_id`` 下），首个
    不达标的干扰项即抛 ``ModelRetry``（**短路**：无需评完剩余项就知道本版不合格），反馈进下一次
    出题上下文让重试换更有迷惑性的干扰项。全部达标则静默返回、调用方采用该 MC。``judge_distractor``
    自身有界重试用尽会抛 ``JudgeError``（DEGRADED），本函数不拦截、任其冒泡由上层
    ``RecoveryPolicy`` 优雅降级（不与"干扰项太弱"的确定性重生成信号混淆）。**仅在
    ``quality_floor is not None`` 时被调用**，故只有高档（4/5）题会走到这里——成本护栏见
    ``generate_multiple_choice`` docstring。
    """
    correct_answer = mc.options[mc.answer_index]
    for index, option in enumerate(mc.options):
        if index == mc.answer_index:
            continue
        verdict = await judge_distractor(
            item,
            question=mc.question,
            correct_answer=correct_answer,
            distractor=option,
            provider=provider,
            emitter=emitter,
            parent_span_id=parent_span_id,
        )
        if not distractor_meets_floor(verdict.label, quality_floor):
            raise ModelRetry(
                f"干扰项「{option}」质量不足：judge 判为「{verdict.label}」，本题难度档要求至少"
                f"「{quality_floor}」——请换一个更有迷惑性、需真懂概念才能排除的干扰项"
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
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        mc = MultipleChoiceQuestion.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc
    # 可判卷门：选项 ≥ 2、正确项下标合法——否则确定性判卷无从比对。
    if len(mc.options) < 2:
        raise ModelRetry(f"选项至少需 2 项（现 {len(mc.options)} 项）：选择题需可区分的干扰项")
    if not 0 <= mc.answer_index < len(mc.options):
        raise ModelRetry(
            f"answer_index 越界：{mc.answer_index} 不在合法下标 [0, {len(mc.options)}) 内"
        )
    # 选项须两两可区分：确定性判卷按文本比对，重复选项会让"选了干扰项"被误判为对、污染薄弱账本。
    # （空 / 纯空白选项已由 options 的 NonEmptyStr 挡下。）
    if len(set(mc.options)) != len(mc.options):
        raise ModelRetry(f"选项含重复文本：{mc.options}——确定性判卷按文本比对，选项须两两可区分")
    # 反-tell 门（缝 3）：只挡表面泄漏、不测 plausibility——干扰项 plausibility（"不懂概念能否排除"）
    # 的真打分是 Tier 2 LLM-judge，显式不在本 issue。放在可判卷门之后（options ≥ 2、answer_index
    # 合法已保证），故可安全区分正确项 / 干扰项。prompt 已把这些从软约束升为硬约束，这两道确定性门
    # 只兜底 egregious 泄漏，阈值保守到不误伤既有平衡假选项。
    # (a) meta 选项禁令（"以上都对 / 都不对 / all of the above" 等）。
    if has_meta_option(mc.options):
        raise ModelRetry(
            "含 meta 选项（如'以上都对 / 都不对 / all of the above'）：每个干扰项须是具体的常见"
            "误解或邻近但错的概念，不得用 meta 选项泄漏题型"
        )
    # (b) 长度离群：正确项 > 最长干扰项 2 倍、或 < 最短干扰项一半（答案被长度出卖）。
    if has_length_outlier(mc.options, mc.answer_index):
        raise ModelRetry(
            "正确项与干扰项长度悬殊（答案被长度出卖）：请让所有选项在长度 / 具体度 / 语法上平行"
        )
    # 刻意不加"题干回声"（stem-echo）门：中文无可靠分词，按字 / n-gram 匹配误报率高（正解与题干
    # 天然共享领域词），易误伤合法题；题干回声只在 prompt 里硬禁，其检测留给 Tier 2 judge。
    # 防幽灵题门（与开放题同规则）：cited_evidence 非空 + 每条都锚定被考 item 真实证据（子串即可）。
    if not mc.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：题必须引用被考知识点的原文证据")
    ghost = ungrounded_citations(mc.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
    # 归一化去重门（缝 3，与开放题同规则）：题干归一化后命中会话内"已问过"台账 → ModelRetry。
    if is_duplicate(mc.question, asked_before):
        raise ModelRetry("与已问过的题重复：请换一个角度提问，不要重复已考过的问题")
    # 选项数硬杠杆门（SE-S5a，缝 3，**仅当调用方按难度档传入 num_options 时生效**）：档越高、要求
    # 选项越多、越难靠排除法蒙对。少于目标数 → ModelRetry，反馈进下一次上下文让重试补足干扰项。
    # 放在所有既有门之后（不与既有 >= 2 / answer_index / 去重 / meta / 长度门抢先报错）；
    # num_options is None（默认路径 / 既有调用方 / eval harness）时**此门整个不参与**——行为与
    # 改动前逐字节等价。
    if num_options is not None and len(mc.options) < num_options:
        raise ModelRetry(
            f"选项数不足：本题难度档要求至少 {num_options} 个选项（现 {len(mc.options)} 项），"
            "请补足有迷惑性、需真懂概念才能排除的干扰项"
        )
    return mc
