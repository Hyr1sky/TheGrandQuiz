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

from collections.abc import Callable
from typing import cast

from grandquiz.domain.learning.assessment import AssessmentResult
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest import IngestResult
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.evals.graders.scorers import (
    expected_bucket_for_language,
    language_consistency,
    no_duplicate,
)
from grandquiz.evals.harness import (
    INGEST_APPROVED_CONCEPTS,
    INGEST_CANDIDATE_COUNT,
    INGEST_RAW_CONTENT,
    MC_CORRECT,
    MC_WRONG,
    SolveResult,
)
from grandquiz.kernel.events import AgentEvent, EventType

Grader = Callable[[SolveResult], list[str]]

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
    return list(sr.context.get("items", []))


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
        stored == INGEST_APPROVED_CONCEPTS,
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
    # span 树（family 4）：ingest 为根，Reader 的 model span 挂其下；点事件挂 ingest 根、不进树。
    _check(failures, len(sr.spans) == 1, f"应只有 1 个根 span，实为 {len(sr.spans)}")
    if sr.spans:
        root = sr.spans[0]
        _check(failures, root.type == "ingest", f"根 span 应为 ingest，实为 {root.type}")
        child_types = [c.type for c in root.children]
        _check(failures, child_types == ["model"], f"子 span 应为 [model]，实为 {child_types}")
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
    item_ids = sr.context.get("item_ids", [])
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
    item_ids = sr.context.get("item_ids", [])
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
            _check(failures, item.summary in correct, "正解应含被考 item 的摘要")
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
    weak_target = sr.context.get("weak_target")
    natural = sr.context.get("natural")
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
    target = sr.context.get("weak_target")
    if not isinstance(target, str):
        return [*failures, f"case 6 应预置薄弱 target，实为 {target!r}"]
    natural = sr.context.get("natural")
    # 前置半（第一次答对 → 观察中、仍在表内）：由预置 [错, 对] 经真实状态机建立，跑 assess 前捕获。
    _check(failures, sr.context.get("pre_state") == "观察中", "前置：第一次答对应转观察中")
    _check(failures, sr.context.get("pre_in_weak") is True, "前置：观察中仍应在薄弱表内")
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
    weak_target = sr.context.get("weak_target")
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
    result = _assess(sr)
    if result is None:
        return [f"result 不是 AssessmentResult：{sr.result!r}"]
    item_ids = sr.context.get("item_ids", [])
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
    weak_target = sr.context.get("weak_target")
    natural = sr.context.get("natural")
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
    recently_asked = cast("dict[str, list[str]]", sr.context.get("recently_asked", {}))
    if isinstance(weak_target, str):
        ledger = [str(q) for q in recently_asked.get(weak_target, [])]
        _check(
            failures,
            ledger == asked_texts,
            f"已问过台账应逐字记录两轮实发题目，实为 {ledger}（应为 {asked_texts}）",
        )
    # 核心断言：会话内所有 QUESTION_ASKED 归一化后零逐字重复（02 回归探针）。
    failures.extend(no_duplicate(sr))
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
}
