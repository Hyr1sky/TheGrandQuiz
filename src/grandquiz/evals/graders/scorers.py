"""两个 Tier-1 质量回归 scorer——语言一致性 + 无重复（可复用、零 token、确定性）。

它们把 01（语言可配置）/ 02（无重复出题）两处 dogfood 修复的行为**在 eval 层变成持续回归守门**：
读一次会话发射的 ``QUESTION_ASKED`` 事件流，纯规则断言、不调任何 LLM、逐字节可回放。任一修复被
删除，配套的假 provider 就会让对应 scorer 变红（见 ``cases/`` 的 case9 / case10 与其单测）。

- ``language_consistency(sr, expected)``：对每个 ``QUESTION_ASKED`` 的 question（MC 再加每个
  option）按 CJK 字符比例分桶（zh / en / mixed），断言（a）每桶 == ``expected``、（b）全会话同一桶
  （跨轮稳定）——正是 01 所修语言漂移的复发探针。
- ``no_duplicate(sr)``：复用 ``domain/learning/question.py`` 的公有 ``dedup_key`` 把会话内所有
  ``QUESTION_ASKED`` 的 question 归一化后断言零逐字重复——正是 02 所修重复出题的复发探针。

两者均返回**失败明细列表**（空 = 通过），与 ``graders/rules.py`` 的既有 grader 同形，可直接被 case
grader 组合调用，也可被 harness 之外的 tests 拿合成事件流直测（缝 2）。
"""

from __future__ import annotations

from typing import cast

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.question import dedup_key
from grandquiz.evals.harness import SolveResult
from grandquiz.kernel.events import AgentEvent

# 语言桶阈值——与 tests/test_question_language._cjk_ratio 的判据同源（zh: 汉字占绝对多数；
# en: 几乎无汉字；两者之间 = mixed）。用比例分桶而非 len(set)==1，对返回常量的假 provider 才有
# 回归保护：删掉语言注入 → 输出英文题 → 桶变 en → 与 expected=zh 不符、变红。
_ZH_MIN_RATIO = 0.6
_EN_MAX_RATIO = 0.1


def cjk_ratio(text: str) -> float:
    """非空白字符里汉字的占比——中文文本趋 1、英文文本趋 0（放 evals 的语言分桶辅助）。"""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    han = sum(1 for c in chars if "一" <= c <= "鿿")
    return han / len(chars)


def language_bucket(text: str) -> str:
    """把一段文本按 CJK 比例分到 ``"zh"`` / ``"en"`` / ``"mixed"`` 桶。"""
    ratio = cjk_ratio(text)
    if ratio > _ZH_MIN_RATIO:
        return "zh"
    if ratio < _EN_MAX_RATIO:
        return "en"
    return "mixed"


_LANGUAGE_TO_BUCKET = {"中文": "zh", "英文": "en"}


def expected_bucket_for_language(language: str) -> str:
    """把 case 的出题语言（"中文" / "英文"，经 question_language 偏好下传）映射到桶（"zh" / "en"）。

    让 ``language_consistency`` 的期望桶由 case 的语言**派生**、而非在 grader 里硬编码——消除
    yaml ↔ grader 语言约定漂移的隐患（未知语言退化为 "zh"，与"中文"兜底一致，ADR-0005）。
    """
    return _LANGUAGE_TO_BUCKET.get(language, "zh")


def _questions_asked(events: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in events if e.type == LearningEvent.QUESTION_ASKED]


def _strings_of(asked: AgentEvent) -> list[str]:
    # 一条 QUESTION_ASKED 需断言语言的全部文本：question 恒有，MC 另有每个 option（用户视图）。
    strings = [str(asked.payload.get("question", ""))]
    options = asked.payload.get("options")
    if isinstance(options, list):
        strings.extend(str(o) for o in cast("list[object]", options))
    return strings


def language_consistency(sr: SolveResult, expected: str) -> list[str]:
    """断言每个 QUESTION_ASKED 的 question / options 都落在 ``expected`` 语言桶、且全会话同一桶。

    ``expected`` 是 ``language_bucket`` 的值（``"zh"`` / ``"en"`` / ``"mixed"``），由 case 按其
    ``task.language`` 给定。返回失败明细列表（空 = 通过）。
    """
    failures: list[str] = []
    asked = _questions_asked(sr.events)
    if not asked:
        return ["无 QUESTION_ASKED——无法判语言一致性"]
    buckets: set[str] = set()
    for event in asked:
        for text in _strings_of(event):
            bucket = language_bucket(text)
            buckets.add(bucket)
            if bucket != expected:
                failures.append(f"语言桶 {bucket} != 期望 {expected}：{text!r}")
    if len(buckets) > 1:
        failures.append(f"全会话语言不一致——出现多桶 {sorted(buckets)}（应跨轮同桶）")
    return failures


def no_duplicate(sr: SolveResult) -> list[str]:
    """断言会话内所有 QUESTION_ASKED 的 question 归一化后零逐字重复（复用公有 ``dedup_key``）。

    归一化吸收空白 / 标点 / 大小写 / 全半角差异（见 ``dedup_key``），故连续两轮"只换标点"的同一道题
    也会被抓住；语义近重复属 Tier 2 LLM-judge，不在此确定性门内。返回失败明细列表（空 = 通过）。
    """
    failures: list[str] = []
    seen: dict[str, str] = {}
    for event in _questions_asked(sr.events):
        question = str(event.payload.get("question", ""))
        key = dedup_key(question)
        if key in seen:
            failures.append(f"会话内逐字重复出题：{question!r} 与先前 {seen[key]!r} 归一化相等")
        else:
            seen[key] = question
    return failures
