"""按 case id 键控的规则 scorer——机械地把 ``test_assessment`` / ``test_ingest`` 里已有断言重表述。

每个 grader 读一个 ``SolveResult``，返回**失败明细列表**（空 = 通过）。断言分五族（decision 5）：

1. 有序事件类型序列——由 runner 从 YAML 的 ``expected_events`` 校验，不在此重复。
2. payload 字段相等——事件 payload 的具体字段（question_type / from_state / cited_evidence…）。
3. 记忆 / 存储状态——Learning Memory 末态、store 里入库了什么。
4. span 树形状——根 span 类型、子 span 类型序列、点事件的 parent 链。
5. provider 调用 / 角色——``calls`` 次数与 ``roles`` 分槽（判卷是否被调用）。

不造通用 YAML 断言 DSL：结构断言就是这些直白的 Python 检查（YAGNI）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from grandquiz.domain.learning.assessment.engine import AssessmentResult
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.grounded_answer import GroundedAnswerResult
from grandquiz.domain.learning.ingest import IngestResult
from grandquiz.domain.learning.models import KnowledgeItem, LearningResource
from grandquiz.domain.learning.tools.web_search_tool import SearchToolResult
from grandquiz.evals.case import AssessCase
from grandquiz.evals.fixture import (
    INGEST_APPROVED_CONCEPTS,
    INGEST_CANDIDATE_COUNT,
    INGEST_RAW_CONTENT,
    MC_CORRECT,
    MC_WRONG,
)
from grandquiz.evals.graders.scorers import (
    expected_bucket_for_language,
    language_consistency,
    no_duplicate,
)
from grandquiz.evals.result import (
    AssessObservation,
    ReactObservation,
    SolveResult,
    WebAcquisitionObservation,
)
from grandquiz.kernel.events import AgentEvent, EventType

Grader = Callable[[SolveResult], list[str]]

_ASSESSMENT_STARTED = "assessment.started"
_ASSESSMENT_ENDED = "assessment.ended"


def _check(failures: list[str], cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def _find(events: list[AgentEvent], etype: str) -> AgentEvent | None:
    return next((e for e in events if e.type == etype), None)


def _find_all(events: list[AgentEvent], etype: str) -> list[AgentEvent]:
    return [e for e in events if e.type == etype]


def _assess(sr: SolveResult) -> AssessmentResult | None:
    return sr.result if isinstance(sr.result, AssessmentResult) else None


def _ingest(sr: SolveResult) -> IngestResult | None:
    return sr.result if isinstance(sr.result, IngestResult) else None


def _items(sr: SolveResult) -> list[KnowledgeItem]:
    observation = _assess_observation(sr)
    return list(observation.items)


def _assess_observation(sr: SolveResult) -> AssessObservation:
    if not isinstance(sr.observation, AssessObservation):
        raise TypeError(f"assess grader received {type(sr.observation).__name__}")
    return sr.observation


def _react_observation(sr: SolveResult) -> ReactObservation:
    if not isinstance(sr.observation, ReactObservation):
        raise TypeError(f"react grader received {type(sr.observation).__name__}")
    return sr.observation


def _web_acquisition_observation(sr: SolveResult) -> WebAcquisitionObservation:
    if not isinstance(sr.observation, WebAcquisitionObservation):
        raise TypeError(f"web acquisition grader received {type(sr.observation).__name__}")
    return sr.observation


# --- case 1：深读产出未经审批 → 未审批的 KnowledgeItem 不得入库 -------------------------------


def grade_case1(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _ingest(sr)
    if result is None:
        return [f"result 不是 IngestResult：{sr.result!r}"]
    _check(failures, result.status == "read", f"status 应为 read，实为 {result.status}")
    stored = [i.concept for i in sr.store.items_for_resource(result.resource_id)]
    _check(
        failures,
        set(stored) == set(INGEST_APPROVED_CONCEPTS),
        f"入库概念应为 {INGEST_APPROVED_CONCEPTS}（仅获批），实为 {stored}",
    )
    _check(failures, len(result.items) == 2, f"result.items 应为 2 个，实为 {len(result.items)}")
    extracted = _find(sr.events, LearningEvent.ITEMS_EXTRACTED)
    n = len(extracted.payload["candidates"]) if extracted is not None else -1
    _check(
        failures,
        n == INGEST_CANDIDATE_COUNT,
        f"审批前预览应含全部 {INGEST_CANDIDATE_COUNT} 候选，实为 {n}",
    )
    # 存储状态（family 3）：资源持久化了原始内容 + hash、status=read、仍不可信。
    resource = sr.store.get_resource(result.resource_id)
    if resource is None:
        failures.append("资源未持久化")
    else:
        _check(failures, resource.status == "read", f"资源 status 应 read，实为 {resource.status}")
        _check(
            failures,
            resource.raw_content == INGEST_RAW_CONTENT,
            "资源未回填原始内容",
        )
        _check(failures, resource.content_hash is not None, "资源缺 content_hash")
        _check(failures, resource.trusted is False, "抓取内容不应标记为可信")
    # span 树（family 4）：ingest → 自然节点批次 → Reader model；点事件挂 ingest 根。
    _check(failures, len(sr.spans) == 1, f"应只有 1 个根 span，实为 {len(sr.spans)}")
    if sr.spans:
        root = sr.spans[0]
        _check(failures, root.type == "ingest", f"根 span 应为 ingest，实为 {root.type}")
        child_types = [c.type for c in root.children]
        _check(
            failures,
            child_types == ["learning.reader_batch"],
            f"子 span 应为 [learning.reader_batch]，实为 {child_types}",
        )
        if root.children:
            model_types = [child.type for child in root.children[0].children]
            _check(
                failures,
                model_types == ["model"],
                f"批次子 span 应为 [model]，实为 {model_types}",
            )
        for etype in (
            LearningEvent.RESOURCE_CREATED,
            LearningEvent.RESOURCE_READ,
            LearningEvent.ITEM_CREATED,
        ):
            point = _find(sr.events, etype)
            if point is None:
                failures.append(f"缺点事件 {etype}")
                continue
            _check(failures, point.span_id is None, f"{etype} 不应开 span")
            _check(
                failures,
                point.parent_span_id == root.span_id,
                f"{etype} 的 parent 应为 ingest 根",
            )
    # provider 调用（family 5）：Reader 深读 1 次。
    _check(failures, sr.calls == 1, f"provider 应被调 1 次（深读），实为 {sr.calls}")
    return failures


# --- case 7：深读 fetch 失败 → 资源标记失败，不产生幽灵 KnowledgeItem ---------------------------


def grade_case7(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _ingest(sr)
    if result is None:
        return [f"result 不是 IngestResult：{sr.result!r}"]
    _check(failures, result.status == "failed", f"status 应为 failed，实为 {result.status}")
    _check(failures, result.items == [], "失败分支不应产 item")
    types = {e.type for e in sr.events}
    _check(failures, EventType.MODEL_STARTED not in types, "fetch 失败不应有 model span")
    _check(failures, LearningEvent.ITEM_CREATED not in types, "fetch 失败不应有 item_created")
    _check(failures, sr.calls == 0, f"fetch 失败深读不应发生，provider 调用应为 0，实为 {sr.calls}")
    resource = sr.store.get_resource(result.resource_id)
    if resource is None:
        failures.append("资源未持久化")
    else:
        _check(failures, resource.status == "failed", f"资源应标 failed，实为 {resource.status}")
    _check(
        failures,
        sr.store.items_for_resource(result.resource_id) == [],
        "失败资源不应有任何 item（无幽灵 item）",
    )
    return failures


# --- case 2：空库时"考我" → 拒绝出题、不凭空编题（不调任何 LLM、不碰记忆）------------------------


def grade_case2(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    _check(failures, result.status == "refused", f"status 应为 refused，实为 {result.status}")
    _check(
        failures,
        result.item_id is None
        and result.verdict is None
        and result.weak_item_id is None
        and result.concept_state is None
        and result.question_type is None,
        "refused 时各字段应全为 None",
    )
    refused = _find(sr.events, LearningEvent.ASSESSMENT_REFUSED)
    reason = refused.payload.get("reason") if refused is not None else None
    _check(failures, reason == "empty_kb", f"拒答理由应为 empty_kb，实为 {reason}")
    types = {e.type for e in sr.events}
    _check(failures, EventType.MODEL_STARTED not in types, "空库拒答不应调任何 LLM")
    _check(
        failures,
        LearningEvent.CONCEPT_STATE_CHANGED not in types,
        "空库拒答不应有状态转移",
    )
    _check(failures, sr.calls == 0, f"空库拒答 provider 调用应为 0，实为 {sr.calls}")
    _check(failures, sr.memory.weak_item_ids() == set(), "空库拒答不应碰记忆")
    _check(failures, len(sr.spans) == 1, f"应只有 1 个根 span，实为 {len(sr.spans)}")
    if sr.spans:
        root = sr.spans[0]
        _check(failures, root.type == "assessment", f"根 span 应为 assessment，实为 {root.type}")
        _check(failures, root.children == [], "拒答不应有子 span")
        _check(failures, root.end_ts is not None, "assessment span 应已闭合")
    return failures


def _grounded_citation(sr: SolveResult, asked: AgentEvent) -> bool:
    # cited_evidence 逐字属于被考 item 自己的证据（子串锚定，防幽灵题）——case 3。
    item_id = asked.payload.get("item_id")
    quotes = {ev.quote for it in _items(sr) if it.item_id == item_id for ev in it.evidence}
    cited: list[str] = list(asked.payload.get("cited_evidence") or [])
    return bool(cited) and all(any(c in q for q in quotes) for c in cited)


# --- case 3：出题 → 每道题锚定存在的 KnowledgeItem，且其 evidence 非空（首次接触 → 选择题）-------


def grade_case3(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    item_ids = _assess_observation(sr).item_ids
    _check(failures, result.status == "judged", f"status 应为 judged，实为 {result.status}")
    _check(failures, result.question_type == "选择题", f"应路由选择题，实为 {result.question_type}")
    _check(failures, result.verdict == "对", f"选对应判对，实为 {result.verdict}")
    asked = _find(sr.events, LearningEvent.QUESTION_ASKED)
    if asked is None:
        return [*failures, "缺 QUESTION_ASKED"]
    _check(
        failures,
        asked.payload.get("question_type") == "选择题",
        "QUESTION_ASKED 的 question_type 应为选择题",
    )
    _check(failures, asked.payload.get("item_id") in item_ids, "出题应锚定真实存在的 item")
    _check(
        failures,
        asked.payload.get("options") == [MC_CORRECT, MC_WRONG],
        "MC 应带 options（用户视图）",
    )
    _check(failures, "answer_index" not in asked.payload, "不应把答案键泄露给用户视图")
    _check(failures, _grounded_citation(sr, asked), "cited_evidence 应逐字锚定被考 item 的证据")
    # provider 分槽（family 5）：MC 判卷是确定性代码，provider 只被调 1 次（出题 enrich）。
    _check(failures, sr.calls == 1, f"MC 应只调 provider 1 次（出题），实为 {sr.calls}")
    _check(failures, sr.roles == ["enrich"], f"应只出题（enrich），无判卷调用，实为 {sr.roles}")
    if sr.spans:
        child_types = [c.type for c in sr.spans[0].children]
        _check(failures, child_types == ["model"], f"MC 只应有出题 model span，实为 {child_types}")
    return failures


# --- case 4：答错一道题 → 对应薄弱概念按 item id 写入 Learning Memory --------------------------


def grade_case4(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    target = result.item_id
    if target is None:
        return [*failures, "judged 结果应带 item_id"]
    item_ids = _assess_observation(sr).item_ids
    _check(failures, target in item_ids, "被考 item 应存在")
    _check(failures, result.question_type == "选择题", f"应为选择题，实为 {result.question_type}")
    _check(failures, result.verdict == "错", f"选错应判错，实为 {result.verdict}")
    # 记忆状态（family 3）：按 item_id 锚定入薄弱，且只此一个（未污染其它 item）。
    _check(failures, sr.memory.state_of(target) == "薄弱", "答错概念应入薄弱")
    _check(failures, sr.memory.weak_item_ids() == {target}, "薄弱表应只含该 item")
    _check(failures, result.concept_state == "薄弱", "result.concept_state 应为薄弱")
    # 代码记账（ADR-0004 "LLM 判卷，代码记账"）：weak_item_id 由代码按 verdict 算，既落 result 也进
    # ANSWER_JUDGED payload——两处都断言，否则把该字段置空的回归能在只查 memory 的 grader 下蒙混过关
    # （memory 由另一条 record_verdict 路径填充）。
    _check(
        failures,
        result.weak_item_id == target,
        f"result.weak_item_id 应为被考 item {target}（代码记账），实为 {result.weak_item_id}",
    )
    judged = _find(sr.events, LearningEvent.ANSWER_JUDGED)
    if judged is None:
        failures.append("缺 ANSWER_JUDGED")
    else:
        _check(
            failures,
            judged.payload.get("weak_item_id") == target,
            "ANSWER_JUDGED 的 weak_item_id 应为被考 item（代码记账进事件流）",
        )
    changed = _find(sr.events, LearningEvent.CONCEPT_STATE_CHANGED)
    if changed is None:
        failures.append("缺 CONCEPT_STATE_CHANGED")
    else:
        p = changed.payload
        _check(failures, p.get("item_id") == target, "转移事件应锚定被考 item")
        _check(failures, p.get("from_state") is None, "from_state 应为 None（首次接触）")
        _check(failures, p.get("to_state") == "薄弱", "to_state 应为薄弱")
        _check(failures, p.get("consecutive_correct") == 0, "连对数应为 0")
    followup = _find(sr.events, LearningEvent.FOLLOWUP_GIVEN)
    if followup is None:
        failures.append("答错应后置追问给正解")
    else:
        _check(failures, followup.payload.get("item_id") == target, "追问应锚定被考 item")
        item = next((it for it in _items(sr) if it.item_id == target), None)
        correct = str(followup.payload.get("correct_answer", ""))
        if item is not None:
            _check(
                failures,
                any(evidence.quote in correct for evidence in item.evidence),
                "本题正解应含被考 item 的原文依据",
            )
    # 时序（family 2）：ANSWER_JUDGED < CONCEPT_STATE_CHANGED < FOLLOWUP_GIVEN < ended。
    types = [e.type for e in sr.events]
    order = [
        LearningEvent.ANSWER_JUDGED,
        LearningEvent.CONCEPT_STATE_CHANGED,
        LearningEvent.FOLLOWUP_GIVEN,
        _ASSESSMENT_ENDED,
    ]
    if all(t in types for t in order):
        idx = [types.index(t) for t in order]
        _check(failures, idx == sorted(idx), f"事件时序不符：{order}")
    return failures


# --- case 5：覆盖优先（mixed 默认，R1-S7 锁死回归）——有薄弱但仍有未考过 → 选未考过、不锁死薄弱 --


def grade_case5(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    observation = _assess_observation(sr)
    weak_target = observation.weak_target_item_id
    natural = observation.natural_item_id
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    asked = _find(sr.events, LearningEvent.QUESTION_ASKED)
    if asked is None:
        return ["缺 QUESTION_ASKED"]
    # 覆盖优先：本会话未考过任何 item → mixed 选未考过的自然选择项 natural，
    # 不回锁到已薄弱的 non_natural（旧排他策略会锁 weak_target → 此两断言把它杀掉）。
    _check(
        failures,
        asked.payload.get("item_id") == natural,
        f"覆盖优先应选未考过的 natural {natural}，实为 {asked.payload.get('item_id')}",
    )
    _check(
        failures,
        asked.payload.get("item_id") != weak_target,
        "覆盖优先不应锁死薄弱 item（dogfood '6 题锁死同一 item' 回归）",
    )
    # natural 未追踪 → 路由选择题（不再因薄弱一律走追问）；确定性判卷，无判卷 model span。
    _check(
        failures,
        asked.payload.get("question_type") == "选择题",
        f"未考过的未追踪概念应路由选择题，实为 {asked.payload.get('question_type')}",
    )
    _check(
        failures,
        result.question_type == "选择题",
        f"result.question_type 应为选择题，实为 {result.question_type}",
    )
    # MC 判卷是确定性代码：无判卷 basic 调用（route 未把 natural 分流到 LLM 判卷路径）。
    _check(failures, sr.roles == ["enrich"], f"MC 路径应只出题（enrich）、无判卷，实为 {sr.roles}")
    return failures


# --- case 6：focus="weak" 复习薄弱 + 三态销账——第一次答对转观察中；连续第二次答对 → 销账移出 ----


def grade_case6(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    observation = _assess_observation(sr)
    target = observation.weak_target_item_id
    if not isinstance(target, str):
        return [*failures, f"case 6 应预置薄弱 target，实为 {target!r}"]
    natural = observation.natural_item_id
    # 前置半（第一次答对 → 观察中、仍在表内）：由预置 [错, 对] 经真实状态机建立，跑 assess 前捕获。
    _check(failures, observation.pre_weak_state == "观察中", "前置：第一次答对应转观察中")
    _check(failures, observation.pre_in_weak is True, "前置：观察中仍应在薄弱表内")
    # 本轮（连续第二次答对 → 销账移出）：focus=weak 仍锁定 target（观察中在薄弱集），观察中 → 开放。
    _check(failures, result.item_id == target, "focus=weak 复考应锁定该薄弱 item")
    _check(failures, result.item_id != natural, "focus=weak 应压过覆盖优先 / 全集随机")
    _check(failures, result.question_type == "开放", f"观察中应走开放，实为 {result.question_type}")
    _check(failures, result.verdict == "对", f"应判对，实为 {result.verdict}")
    _check(failures, sr.memory.state_of(target) is None, "连对第二次应销账（移出记忆）")
    _check(failures, target not in sr.memory.weak_item_ids(), "销账后不应仍在薄弱表")
    _check(failures, result.concept_state is None, "销账后 concept_state 应为 None")
    changed = _find(sr.events, LearningEvent.CONCEPT_STATE_CHANGED)
    if changed is None:
        failures.append("缺 CONCEPT_STATE_CHANGED")
    else:
        p = changed.payload
        _check(
            failures,
            (p.get("from_state"), p.get("to_state"), p.get("consecutive_correct"))
            == ("观察中", "销账", 2),
            f"销账转移应为 (观察中, 销账, 2)，实为 "
            f"{(p.get('from_state'), p.get('to_state'), p.get('consecutive_correct'))}",
        )
    return failures


# --- case 8：题型路由 → 首次接触概念出选择题，薄弱概念复考走追问 -------------------------------


def grade_case8(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    weak_target = _assess_observation(sr).weak_target_item_id
    asked = _find(sr.events, LearningEvent.QUESTION_ASKED)
    if asked is None:
        return ["缺 QUESTION_ASKED"]
    # 薄弱概念复考 → 追问（route_question_type 的薄弱分支被真实走到）。
    _check(
        failures,
        asked.payload.get("item_id") == weak_target,
        f"薄弱优先应锁定 {weak_target}，实为 {asked.payload.get('item_id')}",
    )
    _check(
        failures,
        asked.payload.get("question_type") == "追问",
        f"薄弱概念复考应路由到追问，实为 {asked.payload.get('question_type')}",
    )
    _check(failures, result.question_type == "追问", "result.question_type 应为追问")
    # 判卷走 LLM（basic 槽）——追问不是选择题，故有判卷调用（route 真的分流到非 MC 路径）。
    _check(failures, "basic" in sr.roles, f"追问应走 LLM 判卷（basic 槽），实为 {sr.roles}")
    return failures


# --- case 9：语言一致性（01 回归探针）——多轮英文 task，每题 question / options 英文且全会话同桶 ---


def grade_case9(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    if not isinstance(sr.case, AssessCase):
        return [f"case9 应为 AssessCase，实为 {type(sr.case).__name__}"]
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    item_ids = _assess_observation(sr).item_ids
    asked = _find_all(sr.events, LearningEvent.QUESTION_ASKED)
    _check(failures, len(asked) == 2, f"应为多轮（2 轮）出题，实得 {len(asked)} 题")
    for event in asked:
        _check(
            failures,
            event.payload.get("question_type") == "选择题",
            f"每轮应路由选择题（答对未追踪概念保持 MC），实为 {event.payload.get('question_type')}",
        )
        _check(failures, event.payload.get("item_id") in item_ids, "出题应锚定真实存在的 item")
    # 核心断言：每题 question / options 都落 task 语言对应的桶、且全会话同桶（01 回归探针）。
    # 期望桶由 case 的 task 语言派生（而非硬编码 "en"），消除 yaml ↔ grader 语言约定漂移。
    failures.extend(language_consistency(sr, expected_bucket_for_language(sr.case.language)))
    return failures


# --- case 10：无重复出题（02 回归探针）——复考同一薄弱 item 两轮，题面不逐字重复且薄弱优先未破 -----


def grade_case10(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    observation = _assess_observation(sr)
    weak_target = observation.weak_target_item_id
    natural = observation.natural_item_id
    asked = _find_all(sr.events, LearningEvent.QUESTION_ASKED)
    _check(failures, len(asked) == 2, f"应为多轮（2 轮）复考，实得 {len(asked)} 题")
    # 薄弱优先未破：两轮都锁定同一预置薄弱 item，且它不是全集随机的自然选择（薄弱优先压过随机）。
    for event in asked:
        _check(
            failures,
            event.payload.get("item_id") == weak_target,
            f"复考应锁定薄弱 item {weak_target}，实为 {event.payload.get('item_id')}",
        )
        _check(
            failures,
            event.payload.get("item_id") != natural,
            "薄弱优先应压过全集随机（被考 item 不应是自然选择）",
        )
    # 代码记账命门：会话内"已问过"台账记的内容 == 实际发出的题目文本（顺序一致）——仅断长度会放过
    # "记错内容"的 mutation（如记常量 / 截断），届时下一轮去重门拿垃圾比对、真重复漏网。
    asked_texts = [str(e.payload.get("question", "")) for e in asked]
    if isinstance(weak_target, str):
        ledger = list(observation.questions_for(weak_target))
        _check(
            failures,
            ledger == asked_texts,
            f"已问过台账应逐字记录两轮实发题目，实为 {ledger}（应为 {asked_texts}）",
        )
    # 核心断言：会话内所有 QUESTION_ASKED 归一化后零逐字重复（02 回归探针）。
    failures.extend(no_duplicate(sr))
    return failures


# --- case 11：scope-honor（GKB-S7，修 #1 考错库）——scope=[资源A] → 所有出题 item 属 A，绝不串 B --


def grade_case11(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    _check(failures, result.status == "judged", f"status 应为 judged，实为 {result.status}")
    resource_ids = list(_assess_observation(sr).selected_resource_ids or ())
    allowed = set(resource_ids)
    _check(failures, bool(allowed), "scope-honor 用例应请求非空 scope")
    # 夹具确有 ≥2 资源、且 scope 是其真子集（资源 B 在库但被排除）——否则"绝不串库"断言无意义。
    pool_resources = {it.resource_id for it in _items(sr)}
    _check(failures, len(pool_resources) >= 2, f"多资源夹具应含≥2资源，实为 {pool_resources}")
    _check(
        failures,
        allowed < pool_resources,
        f"scope {allowed} 应是库资源 {pool_resources} 的真子集（库内确有被排除的资源）",
    )
    # 核心断言：每道 QUESTION_ASKED 的 item 都属 scope 内的资源，绝不串到资源 B。
    id_to_resource = {it.item_id: it.resource_id for it in _items(sr)}
    asked = _find_all(sr.events, LearningEvent.QUESTION_ASKED)
    _check(failures, len(asked) >= 1, "应至少出一题")
    for event in asked:
        item_id = str(event.payload.get("item_id"))
        rid = id_to_resource.get(item_id)
        _check(
            failures,
            rid in allowed,
            f"出题 item {item_id}（资源 {rid}）应落在 scope {allowed} 内，绝不串到别的资源",
        )
    # 有效 scope 上脊柱（ASSESSMENT_STARTED payload）——供 trace / eval 断言选了哪库。
    started = _find(sr.events, _ASSESSMENT_STARTED)
    started_scope = started.payload.get("resource_ids") if started is not None else None
    _check(
        failures,
        started_scope == resource_ids,
        f"ASSESSMENT_STARTED 应记有效 scope {resource_ids}，实为 {started_scope}",
    )
    return failures


# --- case 12：empty_scope（GKB-S7，修 #1）——scope 无匹配 → 拒答、零出题、零判卷（不调 provider）--


def grade_case12(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    _check(failures, result.status == "refused", f"status 应为 refused，实为 {result.status}")
    _check(
        failures,
        result.item_id is None
        and result.verdict is None
        and result.weak_item_id is None
        and result.concept_state is None
        and result.question_type is None,
        "refused 时各字段应全为 None",
    )
    refused = _find(sr.events, LearningEvent.ASSESSMENT_REFUSED)
    reason = refused.payload.get("reason") if refused is not None else None
    _check(failures, reason == "empty_scope", f"拒答理由应为 empty_scope，实为 {reason}")
    # 与 case2 的 empty_kb 分野：库非空、仅 scope 命中为空 → empty_scope（不静默考别的库）。
    _check(failures, len(_items(sr)) >= 1, "empty_scope 前提是库非空（否则应为 empty_kb）")
    _check(
        failures,
        bool(_assess_observation(sr).selected_resource_ids),
        "empty_scope 应请求非空 scope",
    )
    types = {e.type for e in sr.events}
    _check(failures, EventType.MODEL_STARTED not in types, "空 scope 拒答不应出题（无 model span）")
    _check(failures, LearningEvent.QUESTION_ASKED not in types, "空 scope 拒答不应出题")
    _check(
        failures,
        LearningEvent.CONCEPT_STATE_CHANGED not in types,
        "空 scope 拒答不应有状态转移",
    )
    _check(
        failures,
        sr.calls == 0,
        f"空 scope 拒答不应调 provider（零出题 / 零判卷），实为 {sr.calls}",
    )
    _check(failures, sr.memory.weak_item_ids() == set(), "空 scope 拒答不应碰记忆")
    _check(failures, len(sr.spans) == 1, f"应只有 1 个根 span，实为 {len(sr.spans)}")
    if sr.spans:
        root = sr.spans[0]
        _check(failures, root.type == "assessment", f"根 span 应为 assessment，实为 {root.type}")
        _check(failures, root.children == [], "拒答不应有子 span")
        _check(failures, root.end_ts is not None, "assessment span 应已闭合")
    return failures


# --- case 13：question_type-honor（GKB-S5/S7，修 #1 错题型；ADR-0006）——"简答"意图盖过自适应→开放 -


def grade_case13(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    _check(failures, result.status == "judged", f"status 应为 judged，实为 {result.status}")
    asked = _find(sr.events, LearningEvent.QUESTION_ASKED)
    if asked is None:
        return [*failures, "缺 QUESTION_ASKED"]
    # fresh / 未追踪 item 自适应本会给"选择题"（routed），但用户显式"简答" → effective="开放"。
    _check(
        failures,
        asked.payload.get("routed") == "选择题",
        f"fresh item 自适应应路由选择题（routed），实为 {asked.payload.get('routed')}",
    )
    _check(
        failures,
        asked.payload.get("effective") == "开放",
        f"显式'简答'应盖过自适应 → effective=开放，实为 {asked.payload.get('effective')}",
    )
    _check(
        failures,
        asked.payload.get("question_type") == "开放",
        "QUESTION_ASKED.question_type（= effective）应为开放",
    )
    _check(
        failures,
        result.question_type == "开放",
        f"result.question_type 应为开放，实为 {result.question_type}",
    )
    # 护栏：短答意图绝不出选择题——开放题无 options（用户视图不含选项），判卷走 LLM（basic 槽）
    # 而非 MC 确定性判卷（若护栏破、误路由选择题，此两断言变红）。
    _check(failures, "options" not in asked.payload, "开放题不应带 options（不出选择题）")
    _check(failures, "basic" in sr.roles, f"开放应走 LLM 判卷（basic 槽），实为 {sr.roles}")
    return failures


# --- case 14：大批量出题不能编造（R2 首个 react 层用例）——2026-07-12 dogfood 逮到的真回归 -------


def grade_case14(sr: SolveResult) -> list[str]:
    """核心不变量：用户要求 N 道题，trace 里必须有恰好一次 ``start_quiz(count=N)`` 的真实工具
    调用，且真跑出 N 条 ``QUESTION_ASKED``——而不是 ReAct 决策层在没有工具调用的情况下，直接在
    最终文本里编一份"考核小结"。三条断言合起来就排除了"编造"这个失败模式：没有真调用 → count 不
    对 → 或问的题数不够，任一条都能抓住回归；不需要对最终文本做任何"像不像编的"模糊启发式判断。
    """
    failures: list[str] = []
    starts = _find_all(sr.events, EventType.TOOL_CALL_STARTED)
    _check(
        failures,
        len(starts) == 1,
        f"应恰好一次 tool_call.started（大批量出题必须真调用一次工具），实为 {len(starts)} 次",
    )
    if not starts:
        return failures
    start = starts[0]
    _check(
        failures,
        start.payload.get("tool_name") == "start_quiz",
        f"唯一的工具调用应是 start_quiz，实为 {start.payload.get('tool_name')}",
    )
    arguments: dict[str, object] = dict(start.payload.get("arguments") or {})
    actual_count: object = arguments.get("count")
    asked = _find_all(sr.events, LearningEvent.QUESTION_ASKED)
    _check(
        failures,
        isinstance(actual_count, int) and actual_count == len(asked),
        f"start_quiz(count={actual_count}) 应等于真实出题数 {len(asked)}"
        "（count 与实跑题数不一致，说明工具调用参数与后续行为脱节）",
    )
    _check(
        failures,
        len(asked) >= 1,
        "应至少真实出了一题（QUESTION_ASKED 缺失 = 工具调用是空转 / 被绕过）",
    )
    ends = _find_all(sr.events, EventType.TOOL_CALL_ENDED)
    _check(
        failures,
        len(ends) == 1 and ends[0].payload.get("ok") is True,
        f"start_quiz 调用应成功结束，实为 {[e.payload.get('ok') for e in ends]}",
    )
    return failures


# --- case 15：自然材料问答必须走有界 workflow 并返回精确 citation -----------------------------


def grade_case15(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    observation = _react_observation(sr)
    expected_resource_id = observation.grounded_resource_id
    full_document_chars = observation.full_document_chars
    starts = _find_all(sr.events, EventType.TOOL_CALL_STARTED)
    grounded_starts = [
        event for event in starts if event.payload.get("tool_name") == "answer_from_documents"
    ]
    _check(
        failures,
        len(starts) == 1 and len(grounded_starts) == 1,
        "自然材料问答应恰好调用一次 answer_from_documents",
    )
    if grounded_starts:
        arguments = dict(grounded_starts[0].payload.get("arguments") or {})
        _check(
            failures,
            arguments.get("resource_ids") == [expected_resource_id],
            f"grounded answer 必须使用 exact selected scope {[expected_resource_id]}，实为 "
            f"{arguments.get('resource_ids')}",
        )

    searches = _find_all(sr.events, LearningEvent.DOCUMENT_NODES_SEARCHED)
    _check(failures, len(searches) == 1, f"应恰好一次节点搜索，实为 {len(searches)}")
    if searches:
        scope = searches[0].payload.get("scope")
        _check(
            failures,
            scope == {"mode": "selected", "resource_ids": [expected_resource_id]},
            f"搜索 scope 必须精确 selected，实为 {scope}",
        )
    reads = [
        event
        for event in _find_all(sr.events, LearningEvent.DOCUMENT_NODE_READ)
        if event.payload.get("ok") is True
    ]
    _check(failures, bool(reads), "自然材料问答至少应有一次成功 bounded read")
    read_chars = sum(
        value for event in reads if isinstance((value := event.payload.get("chars")), int)
    )
    if full_document_chars > 0:
        _check(
            failures,
            read_chars * 4 <= full_document_chars,
            f"读取应不超过全文 25%，实为 {read_chars}/{full_document_chars}",
        )

    citations = [
        event
        for event in _find_all(sr.events, LearningEvent.CITATION_RESOLVED)
        if event.payload.get("source") == "node_read"
    ]
    _check(failures, bool(citations), "最终至少应有一条 exact node citation")
    if searches and reads and citations:
        _check(
            failures,
            searches[0].seq
            < min(event.seq for event in reads)
            < min(event.seq for event in citations),
            "事件顺序必须为 search → read → citation",
        )

    tool_ends = [
        event
        for event in _find_all(sr.events, EventType.TOOL_CALL_ENDED)
        if event.payload.get("ok") is True
    ]
    if len(tool_ends) != 1:
        failures.append(f"grounded tool 应成功结束一次，实为 {len(tool_ends)}")
    else:
        raw_result = tool_ends[0].payload.get("result")
        try:
            if not isinstance(raw_result, str):
                raise ValueError("result 不是 JSON 字符串")
            result = GroundedAnswerResult.model_validate_json(raw_result)
        except Exception as exc:
            failures.append(f"grounded tool 结果无法解析：{exc!r}")
        else:
            _check(
                failures,
                result.status == "answered",
                f"结果状态应 answered，实为 {result.status}",
            )
            _check(failures, bool(result.citations), "工具结果必须包含已验证 citations")
            revision = (
                sr.store.current_revision(expected_resource_id)
                if isinstance(expected_resource_id, str)
                else None
            )
            for citation in result.citations:
                _check(failures, revision is not None, "selected resource 缺 current revision")
                if revision is not None:
                    _check(
                        failures,
                        citation.revision_id == revision.revision_id
                        and revision.raw_content[citation.start_offset : citation.end_offset]
                        == citation.quote,
                        "citation 必须指向 current revision 的逐字 source span",
                    )

    model_ends = _find_all(sr.events, EventType.MODEL_ENDED)
    _check(failures, len(model_ends) <= 4, f"model calls 应 ≤4，实为 {len(model_ends)}")
    total_tokens = 0
    usage_complete = True
    for event in model_ends:
        usage = event.payload.get("usage")
        if isinstance(usage, Mapping):
            token_value = cast("Mapping[str, object]", usage).get("total_tokens")
            if isinstance(token_value, int):
                total_tokens += token_value
                continue
        usage_complete = False
    _check(failures, usage_complete, "每次 model call 都必须记录可核算的 token usage")
    _check(failures, total_tokens <= 45_000, f"累计 tokens 应 ≤45000，实为 {total_tokens}")
    return failures


# --- case 16：Search / Fetch cassette 离线回放 + 质量失败零 KB 污染 ---------------------------


def grade_case16(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    result = _ingest(sr)
    if result is None:
        return [f"result 不是 IngestResult：{sr.result!r}"]
    observation = _web_acquisition_observation(sr)
    selected_url = observation.selected_url
    rejected_url = observation.rejected_url
    rejected = observation.rejected_result
    _check(failures, result.status == "read", f"选中候选应成功入库，实为 {result.status}")
    resource = sr.store.get_resource(result.resource_id)
    _check(failures, resource is not None, "选中候选缺资源快照")
    if resource is not None:
        _check(
            failures,
            resource.url == selected_url,
            "资源 identity 必须保持 selected requested URL",
        )
        _check(failures, resource.trusted is False, "网页正文必须保持不可信标记")
    _check(
        failures,
        {item.concept for item in result.items} == set(INGEST_APPROVED_CONCEPTS),
        "成功候选只应写入获批概念",
    )
    resource_read = _find(sr.events, LearningEvent.RESOURCE_READ)
    _check(failures, resource_read is not None, "成功候选缺 RESOURCE_READ 审计事件")
    if resource_read is not None:
        _check(
            failures,
            resource_read.payload.get("adapter") == "native_http"
            and resource_read.payload.get("extractor") == "trafilatura:2.1.0"
            and cast("Mapping[str, object]", resource_read.payload.get("quality", {})).get(
                "accepted"
            )
            is True,
            "RESOURCE_READ 应记录 adapter / extractor / 质量结论",
        )
        _check(failures, "content" not in resource_read.payload, "trace 不得保存完整网页正文")
    _check(failures, rejected.status == "failed", "challenge 页面必须 fail closed")
    _check(failures, rejected.items == [], "challenge 页面不得产生 KnowledgeItem")
    _check(
        failures,
        sr.store.items_for_resource(rejected.resource_id) == [],
        "质量失败资源不得污染 KB",
    )
    _check(
        failures,
        sr.calls == observation.provider_calls_after_success == 1,
        "质量失败之后 Reader 调用数不得增加",
    )
    failed_events = _find_all(sr.events, LearningEvent.RESOURCE_FETCH_FAILED)
    _check(failures, len(failed_events) == 1, "应恰有一次结构化质量失败事件")
    if failed_events:
        _check(
            failures,
            failed_events[0].payload.get("url") == rejected_url
            and failed_events[0].payload.get("classification") == "bot_challenge",
            "失败事件应锚定被拒 URL 与 bot_challenge 分类",
        )
    return failures


# --- case 17：真实 ReAct 决策必须 search → 人选 → ingest，失败页零污染 ----------------------


def grade_case17(sr: SolveResult) -> list[str]:
    failures: list[str] = []
    starts = _find_all(sr.events, EventType.TOOL_CALL_STARTED)
    names = [event.payload.get("tool_name") for event in starts]
    search_starts = [event for event in starts if event.payload.get("tool_name") == "web_search"]
    ingest_starts = [event for event in starts if event.payload.get("tool_name") == "ingest"]
    _check(
        failures,
        1 <= len(search_starts) <= 3,
        f"应有 1–3 次有界 web_search（允许开放 ReAct 调整 query），实为 {len(search_starts)}",
    )
    _check(failures, len(ingest_starts) == 2, f"应恰好两次 ingest，实为 {len(ingest_starts)}")
    _check(
        failures,
        names == ["web_search"] * len(search_starts) + ["ingest", "ingest"],
        f"所有搜索必须发生在两次 ingest 之前，且不得调用其他工具，实为 {names}",
    )
    if not search_starts or len(ingest_starts) != 2:
        return failures

    success_start, failed_start = ingest_starts
    _check(
        failures,
        all(event.parent_span_id != success_start.parent_span_id for event in search_starts),
        "搜索与成功 ingest 必须分属不同 agent turn，等待用户选择后才能抓取",
    )
    _check(
        failures,
        len({event.parent_span_id for event in search_starts}) == 1,
        "搜索重试应收敛在第一个发现回合，不得跨到用户选择后的回合",
    )
    _check(
        failures,
        success_start.parent_span_id != failed_start.parent_span_id,
        "成功材料与低质量页必须分别由独立用户消息触发",
    )

    ends = _find_all(sr.events, EventType.TOOL_CALL_ENDED)
    _check(
        failures,
        len(ends) == len(starts),
        f"所有工具调用都必须闭合，started={len(starts)} ended={len(ends)}",
    )
    candidate_urls: set[str] = set()
    search_span_ids = {event.span_id for event in search_starts}
    successful_search_ends = [
        event
        for event in ends
        if event.span_id in search_span_ids and event.payload.get("ok") is True
    ]
    for search_end in successful_search_ends:
        raw_search = search_end.payload.get("result")
        try:
            if not isinstance(raw_search, str):
                raise ValueError("search result 不是 JSON 字符串")
            search_result = SearchToolResult.model_validate_json(raw_search)
        except Exception as exc:
            failures.append(f"web_search 结果无法解析：{exc!r}")
        else:
            _check(
                failures,
                search_result.selection_required is True,
                "web_search 结果必须显式要求用户选择",
            )
            candidate_urls.update(result.url for result in search_result.results)
    _check(failures, bool(candidate_urls), "至少一次 web_search 必须返回候选")

    success_args = cast("Mapping[str, object]", success_start.payload.get("arguments") or {})
    failed_args = cast("Mapping[str, object]", failed_start.payload.get("arguments") or {})
    success_url = success_args.get("url")
    failed_url = failed_args.get("url")
    _check(
        failures,
        isinstance(success_url, str) and success_url in candidate_urls,
        f"成功 ingest URL 必须逐字来自搜索候选，实为 {success_url!r}",
    )
    _check(
        failures,
        isinstance(failed_url, str) and failed_url not in candidate_urls,
        "低质量页应是用户显式提供的独立 URL，不得冒充搜索候选",
    )

    if isinstance(success_url, str):
        success_resource = sr.store.get_resource(
            LearningResource.create(url=success_url).resource_id
        )
        _check(failures, success_resource is not None, "成功候选缺资源快照")
        if success_resource is not None:
            _check(
                failures,
                success_resource.status == "read" and success_resource.trusted is False,
                "成功网页应以 read + untrusted 状态入库",
            )
            _check(
                failures,
                bool(sr.store.items_for_resource(success_resource.resource_id)),
                "成功网页必须形成获批 KnowledgeItem",
            )
    if isinstance(failed_url, str):
        failed_resource = sr.store.get_resource(LearningResource.create(url=failed_url).resource_id)
        _check(failures, failed_resource is not None, "失败页缺可审计资源记录")
        if failed_resource is not None:
            _check(failures, failed_resource.status == "failed", "低质量页必须 fail closed")
            _check(
                failures,
                sr.store.items_for_resource(failed_resource.resource_id) == [],
                "低质量页不得污染 KB",
            )

    _check(
        failures,
        len(_find_all(sr.events, LearningEvent.WEB_SEARCH_STARTED)) == len(search_starts)
        and len(_find_all(sr.events, LearningEvent.WEB_SEARCH_ENDED)) == len(search_starts),
        "每次 web_search 都必须在领域事件脊柱上成对闭合",
    )
    _check(
        failures,
        len(_find_all(sr.events, LearningEvent.RESOURCE_FETCH_FAILED)) == 1,
        "低质量页应产生恰好一条结构化 fetch failure",
    )
    _check(
        failures,
        len(_find_all(sr.events, LearningEvent.RESOURCE_APPROVED)) == 1,
        "只有成功候选可以越过 Reader / 审批并获批",
    )
    return failures


GRADERS: dict[str, Grader] = {
    "case1": grade_case1,
    "case2": grade_case2,
    "case3": grade_case3,
    "case4": grade_case4,
    "case5": grade_case5,
    "case6": grade_case6,
    "case7": grade_case7,
    "case8": grade_case8,
    "case9": grade_case9,
    "case10": grade_case10,
    "case11": grade_case11,
    "case12": grade_case12,
    "case13": grade_case13,
    "case14": grade_case14,
    "case15": grade_case15,
    "case16": grade_case16,
    "case17": grade_case17,
}
