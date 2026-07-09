"""Eval harness——规范确定性装配 + Solver + runner + 报告。

设计判断（见包 docstring）：

- **规范确定性装配**：``build_event_harness`` / ``summarize_spans`` 是 ``test_assessment`` 与
  ``test_ingest`` 里 ``_harness`` / ``_summ`` 的唯一权威版本（两测试文件 import 之、删本地重复）。
  ManualClock + 种子化 rng + trace_id="run" 保证逐字节可回放。
- **假 provider（canned JSON）**：``AssessFakeProvider`` / ``IngestFakeProvider`` 镜像两测试文件里
  的假 provider——按 role 分槽、从 messages 回抽真实证据引用、计调用次数与 role。独立于 cassette。
- **Solver 通用适配器**：从一个 ``Case`` 重建确定性前置（种子化 KnowledgeItem 库、预置 Learning
  Memory 状态、ScriptedResponder 作答、rng 种子、ManualClock、canned provider），调既有入口
  （``assess_once`` / ``ingest_resource``）**一次**，捕获发射的 ``AgentEvent`` 列表 + span 树 +
  result + 记忆 / 存储末态。
- **runner + 报告**：per-case pass/fail、token 成本列（汇总 ``MODEL_ENDED`` payload 的
  ``usage.total_tokens``）、prompt 版本（``MODEL_STARTED`` payload 的 ``prompt_version`` =
  ``name@digest``）。``ReplayMiss`` 等 provider 异常在 ``run_case`` 里被记为**硬失败**（``passed``
  恒 False），绝不静默通过。
"""

from __future__ import annotations

import asyncio
import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import yaml

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment import AssessmentResult, assess_once
from grandquiz.domain.learning.grading import VerdictLabel
from grandquiz.domain.learning.ingest import IngestResult, ingest_resource
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
)
from grandquiz.domain.learning.preference import (
    QUESTION_LANGUAGE_KEY,
    DictPreferenceMemory,
    PreferenceMemory,
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.selection import Focus, select_target
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.report import render_trace_html
from grandquiz.kernel.trace import Span, TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, Usage

# --- 规范确定性装配（test_assessment / test_ingest 的 _harness / _summ 权威版本）-------------


def build_event_harness() -> tuple[EventEmitter, list[AgentEvent], TraceStore]:
    """建一套确定性事件装配：内存 TraceStore + 收集列表都订阅同一 sink，ManualClock + trace_id=run。

    返回 ``(emitter, events, trace)``——``events`` 是按序收集的 AgentEvent 列表，``trace`` 可
    ``span_tree("run")`` 投影 span 树（用完 ``close()``）。这是 eval / 单元测试共用的唯一装配。
    """
    events: list[AgentEvent] = []
    trace = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(events.append)
    sink.subscribe(trace.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id="run")
    return emitter, events, trace


def summarize_spans(spans: list[Span]) -> list[dict[str, Any]]:
    """把 span 树折成只含 type / start_ts / end_ts / children 的可比较字典树（回放一致断言用）。"""
    return [
        {
            "type": s.type,
            "start_ts": s.start_ts,
            "end_ts": s.end_ts,
            "children": summarize_spans(s.children),
        }
        for s in spans
    ]


# --- 规范夹具（镜像两测试文件里的常量，集中一处供 Solver 与 graders 复用）----------------------

SEED = 42
ASSESS_URL = "https://example.com/react"
INGEST_URL = "https://example.com/react-hooks"
ALLOWED_DOMAINS = {"example.com"}

# 选择题固定选项（正确项恒在下标 0）——responder 注入其一即可确定性判对 / 判错。
MC_CORRECT = "正确选项"
MC_WRONG = "干扰项"

# 每个 item 一条独一无二的证据引文（互不为子串）——假 provider 据此从 messages 回抽真实证据。
ITEM_DATA: list[tuple[str, str]] = [
    ("闭包", "闭包捕获变量而非值"),
    ("变量提升", "var 声明提升到作用域顶部"),
    ("事件循环", "事件循环调度宏任务与微任务"),
]
QUOTES = {quote for _concept, quote in ITEM_DATA}

# ingest 用 Reader 固定输出：三个候选，审批只放行其中两个（闭包 / 事件循环）。
READER_JSON = json.dumps(
    {
        "topic": "JavaScript 核心机制",
        "candidates": [
            {"concept": "闭包", "summary": "s1", "evidence": [{"quote": "q1"}], "confidence": 0.9},
            {
                "concept": "变量提升",
                "summary": "s2",
                "evidence": [{"quote": "q2"}],
                "confidence": 0.8,
            },
            {
                "concept": "事件循环",
                "summary": "s3",
                "evidence": [{"quote": "q3"}],
                "confidence": 0.7,
            },
        ],
    },
    ensure_ascii=False,
)
INGEST_APPROVED_CONCEPTS = ["闭包", "事件循环"]
INGEST_CANDIDATE_COUNT = 3
INGEST_RAW_CONTENT = "React hooks 深读材料"


# --- 假 provider（canned JSON，镜像两测试文件）------------------------------------------------


class AssessFakeProvider:
    """确定性假 provider（镜像 ``test_assessment._AssessProvider``）：enrich 出题、basic 判卷。

    enrich 出题按 system prompt 分型：MC prompt（含 ``answer_index`` 字样）→ 产选择题 JSON，否则
    （开放 / 追问共用 schema）→ 产开放题 JSON；从 messages 回抽被考 item 的真实证据来引用（防幽灵题
    在真链路上成立）。``verdict`` 只在 basic 判卷槽生效。计每次 role，供角色分槽 / 判卷调用断言。
    """

    def __init__(self, verdict: str) -> None:
        self._verdict = verdict
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        text = "\n".join(m.content for m in messages)
        quote = next(q for q in QUOTES if q in text)  # 只有被考 item 的引文会出现在其 prompt 里
        payload: dict[str, Any]
        if role == "enrich":
            if "answer_index" in text:  # 选择题 prompt → 产 MC JSON（正确项恒在下标 0）
                payload = {
                    "question": "该知识点的核心是什么？",
                    "options": [MC_CORRECT, MC_WRONG],
                    "answer_index": 0,
                    "cited_evidence": [quote],
                }
            else:  # 开放 / 追问 prompt → 产开放题 JSON（共用 schema）
                payload = {"question": "该知识点的核心是什么？", "cited_evidence": [quote]}
        else:  # basic → 判卷
            payload = {"verdict": self._verdict, "cited_evidence": [quote]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


class IngestFakeProvider:
    """返回固定 Reader JSON、计调用次数与 role（镜像 ``test_ingest._FixedProvider``）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        return Completion(text=self.text, usage=Usage(prompt_tokens=7, completion_tokens=3))


def _extract_quote(messages: Sequence[Message]) -> str:
    """从组装好的 messages 里回抽被考 item 的真实证据引文（只有它会出现在自己的 prompt 里）。"""
    text = "\n".join(m.content for m in messages)
    return next(q for q in QUOTES if q in text)


# 语言回声 provider 的常量——镜像 ``tests/test_question_language._LanguageEchoProvider``：题目 / 选项
# 随 task 语言变。question 每轮换角度（provider 内按 enrich 调用序轮换），使复考同一 item 时不撞去重
# 门；两条 question 归一化后互不相等（否则会话内会逐字重复）。选项固定、answer_index 恒 1。
_LANG_ZH_QUESTIONS: tuple[str, ...] = (
    "闭包捕获的是变量本身还是值的快照？",
    "闭包如何延长其引用变量的生命周期？",
)
_LANG_EN_QUESTIONS: tuple[str, ...] = (
    "Does a closure capture the variable itself or a value snapshot?",
    "How does a closure extend the lifetime of the variables it references?",
)
_LANG_ZH_OPTIONS = ["值的快照", "变量本身"]
_LANG_EN_OPTIONS = ["a value snapshot", "the variable itself"]
_LANG_ANSWER_INDEX = 1
# case9 用英文 task：正确项是英文选项的 answer_index 项。健康态下 responder 答它 → 判对 → 概念保持
# 未追踪 → 每轮仍 MC；若语言注入被删（01 回归）→ provider 转产中文选项 → 答案对不上 → 但 case9 的
# language_consistency 早已因"中文题 != 期望 en"变红（真回归信号来自英文 task 的语言漂移）。
LANG_MC_CORRECT = _LANG_EN_OPTIONS[_LANG_ANSWER_INDEX]


class LanguageEchoAssessProvider:
    """出题按 system prompt 里**被替换后**的 ``{{LANGUAGE}}`` 指令决定语言（镜像语言回声测试）。

    这是 01（语言可配置）的回归探针：若删掉语言注入，system prompt 里不会出现"请用 英文"，本 fake
    就退回中文出题——英文 task 下 ``language_consistency(expected="en")`` 随即变红。每次 enrich 调用
    按序轮换问句（两条归一化互不相等），使同一 item 复考时不撞去重门；``cited_evidence`` 恒引被考
    item 的真实证据（与语言无关），使锚定门放行。健康态下每轮都是 MC（判卷走确定性代码、不打 basic
    槽）；但仍实现 basic 判卷分支（恒判对），使 01 一旦回归、路由漂到追问 / 开放时判卷不硬失败，让
    language_consistency 而非 GradingError 成为红灯来源（回归信号落在 scorer 上，更清晰）。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.roles: list[Role] = []
        self._enrich_calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        quote = _extract_quote(messages)
        if role != "enrich":  # basic 判卷：恒判对（健康态 MC 不走这里，仅回归时兜底）
            return Completion(
                text=json.dumps({"verdict": "对", "cited_evidence": [quote]}, ensure_ascii=False),
                usage=Usage(prompt_tokens=7, completion_tokens=3),
            )
        english = "请用 英文" in messages[0].content  # {{LANGUAGE}} 被替换成"英文"的证据
        questions = _LANG_EN_QUESTIONS if english else _LANG_ZH_QUESTIONS
        question = questions[self._enrich_calls % len(questions)]
        self._enrich_calls += 1
        payload = {
            "question": question,
            "options": _LANG_EN_OPTIONS if english else _LANG_ZH_OPTIONS,
            "answer_index": _LANG_ANSWER_INDEX,
            "cited_evidence": [quote],
        }
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


# 去重敏感 provider 的常量——镜像 ``tests/test_no_duplicate._DupProvider``：默认重复同一道题，见到
# 注入的"已问过"约束才换角度。两句归一化后互不相等（换角度是合法的、不该被去重误杀）。
_DEDUP_DEFAULT_Q = "什么是闭包？"
_DEDUP_ALT_Q = "闭包如何捕获它引用的变量？"


class DedupAssessProvider:
    """出题默认**重复**同一道题，仅在 user message 里见到"已问过"约束时才换角度；判卷恒判对。

    这是 02（无重复出题）的回归探针：若删掉去重注入 / 去重门，复考同一薄弱 item 时第二轮拿不到"已问
    过"约束 → 退回默认题 → ``no_duplicate`` 随即变红。判卷恒"对"让薄弱 item 转观察中、仍留在薄弱优先
    集，复考锁定同一 item（薄弱优先未破）。``cited_evidence`` 恒引真实证据使锚定门放行。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        quote = _extract_quote(messages)
        if role == "enrich":  # 出题：见"已问过"约束才换角度，否则默认重复
            text = "\n".join(m.content for m in messages)
            question = _DEDUP_ALT_Q if "已问过" in text else _DEDUP_DEFAULT_Q
            payload: dict[str, Any] = {"question": question, "cited_evidence": [quote]}
        else:  # basic：恒判对（让薄弱 item 转观察中、留在薄弱优先集，复考锁定同一 item）
            payload = {"verdict": "对", "cited_evidence": [quote]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


def build_stocked_store() -> tuple[LearningStore, list[str]]:
    """建一个塞了 ``ITEM_DATA`` 若干 KnowledgeItem 的 store，返回 ``(store, item_ids)``。

    ``LearningTask`` 已消解（ADR-0005）：资源内容寻址（``resource_id = derive_id(ASSESS_URL)``）、
    进全局 KB 单池；出题语言归 Preference Memory（``_solve_assess`` 按 ``case.language`` 设偏好）。
    """
    store = LearningStore()
    resource = LearningResource.create(url=ASSESS_URL)
    store.add_resource(resource)
    items = [
        KnowledgeItem.create(
            resource_id=resource.resource_id,
            index=index,
            concept=concept,
            summary=f"{concept} 的一句话摘要",
            evidence=[Evidence(quote=quote)],
            confidence=0.9,
        )
        for index, (concept, quote) in enumerate(ITEM_DATA)
    ]
    store.add_items(items)
    return store, [item.item_id for item in items]


# --- Case 模型 + YAML 加载 --------------------------------------------------------------------

CASES_DIR = Path(__file__).parent / "cases"


@dataclass(frozen=True)
class PresetVerdict:
    """assess 前置：对某 item 预置一次判决（经真实 ``record_verdict`` 建 Learning Memory 状态）。

    ``target`` 是选择器：``"index:N"``（第 N 个 item）或 ``"non_natural"``（第一个 != 全集随机
    自然选择的 item——用来证明薄弱优先确实压过了全集随机，照 case 5 / 6 的对照手法）。
    """

    target: str
    verdict: str


def _empty_presets() -> list[PresetVerdict]:
    # 显式类型工厂（照 trace.Span._empty_children）：裸 default_factory=list 会被推成 Unknown。
    return []


def _empty_strs() -> list[str]:
    return []


@dataclass(frozen=True)
class Case:
    """一个 eval 用例：case id + 类型 + 输入 / 前置 + 期望的有序事件类型序列。

    更丰富的结构断言（payload 字段、记忆 / 存储末态、span 树形状、provider 调用 / 角色）不进 YAML，
    由 ``graders/`` 里按 ``id`` 键控的 Python scorer 负责（避免造通用 YAML 断言 DSL）。
    """

    id: str
    kind: Literal["ingest", "assess"]
    expected_events: list[str]
    # assess 专属
    stocked: bool = True
    preset: list[PresetVerdict] = field(default_factory=_empty_presets)
    answer: str = "我的作答"
    verdict: str = "对"
    # 多轮 assess：非空时对每个 answer 调 assess_once 一次，跨轮复用同一 memory / store / 会话内
    # recently_asked 台账（镜像 CLI run_quiz 驱动），事件流按序拼接。空 = 单轮（走 ``answer``），
    # 既有 8 用例走此向后兼容路径、行为一字不变。
    answers: list[str] = field(default_factory=_empty_strs)
    # 假 provider 选择：default = canned JSON；language_echo / dedup = 两个新用例的回归探针 fake。
    provider: Literal["default", "language_echo", "dedup"] = "default"
    # task 出题 / 判卷语言（下传到 {{LANGUAGE}} 槽）；默认"中文"使既有用例装配不变。
    language: str = "中文"
    # 选题聚焦（R1-S7）：mixed 覆盖优先（默认）/ new 只考未考过 / weak 复习薄弱。下传 assess_once。
    focus: Focus = "mixed"
    # ingest 专属
    source: Literal["ok", "boom"] = "ok"
    approval_keep: list[str] = field(default_factory=_empty_strs)


def _parse_case(raw: Any) -> Case:
    case_id = str(raw["id"])
    expected = [str(x) for x in raw["expected_events"]]
    setup: Any = raw.get("setup") or {}
    if str(raw["kind"]) == "assess":
        preset = [
            PresetVerdict(target=str(p["target"]), verdict=str(p["verdict"]))
            for p in setup.get("preset", [])
        ]
        raw_provider = str(setup.get("provider", "default"))
        provider: Literal["default", "language_echo", "dedup"] = (
            raw_provider if raw_provider in ("default", "language_echo", "dedup") else "default"
        )
        raw_focus = str(setup.get("focus", "mixed"))
        focus: Focus = raw_focus if raw_focus in ("mixed", "new", "weak") else "mixed"
        return Case(
            id=case_id,
            kind="assess",
            expected_events=expected,
            stocked=bool(setup.get("stocked", True)),
            preset=preset,
            answer=str(setup.get("answer", "我的作答")),
            verdict=str(setup.get("verdict", "对")),
            answers=[str(a) for a in setup.get("answers", [])],
            provider=provider,
            language=str(setup.get("language", "中文")),
            focus=focus,
        )
    src: Literal["ok", "boom"] = "boom" if str(setup.get("source", "ok")) == "boom" else "ok"
    return Case(
        id=case_id,
        kind="ingest",
        expected_events=expected,
        source=src,
        approval_keep=[str(c) for c in setup.get("approval_keep", [])],
    )


def load_cases() -> list[Case]:
    """从 ``cases/*.yaml`` 加载全部用例，按 ``id`` 稳定排序。"""
    cases = [
        _parse_case(yaml.safe_load(p.read_text(encoding="utf-8"))) for p in CASES_DIR.glob("*.yaml")
    ]
    return sorted(cases, key=lambda c: c.id)


# --- Solver（通用适配器：case → 确定性前置 → 调既有入口一次 → 捕获事件 / trace）-----------------


@dataclass
class SolveResult:
    """一次 solve 的产物：供规则 scorer 断言五族的全部素材。"""

    case: Case
    events: list[AgentEvent]
    spans: list[Span]
    result: AssessmentResult | IngestResult | None
    store: LearningStore
    memory: LearningMemory
    calls: int
    roles: list[Role]
    context: dict[str, Any]


def _resolve_answer(token: str) -> str:
    return {
        "mc_correct": MC_CORRECT,
        "mc_wrong": MC_WRONG,
        "lang_correct": LANG_MC_CORRECT,  # 语言用例（英文 task）的正确选项
    }.get(token, token)


class _CountingFake(Protocol):
    """结构类型：本模块的假 assess provider——除 ``complete`` 外还计 ``calls`` / ``roles``。"""

    calls: int
    roles: list[Role]

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion: ...


def _build_assess_fake(case: Case) -> _CountingFake:
    """按 case 选假 provider：默认 canned JSON；language_echo / dedup 是两个新用例的回归探针。"""
    if case.provider == "language_echo":
        return LanguageEchoAssessProvider()
    if case.provider == "dedup":
        return DedupAssessProvider()
    return AssessFakeProvider(case.verdict)


def _resolve_target(selector: str, item_ids: list[str], natural: str) -> str:
    if selector == "non_natural":
        return next(i for i in item_ids if i != natural)
    if selector.startswith("index:"):
        return item_ids[int(selector.split(":", 1)[1])]
    raise ValueError(f"未知 target 选择器：{selector}")


async def _solve_assess(case: Case, provider_override: Provider | None) -> SolveResult:
    memory = LearningMemory()
    context: dict[str, Any] = {}
    # 语言归 Preference Memory（ADR-0005）：case.language 设进 question_language 偏好、下传
    # assess_once（偏好 > 中文）。默认"中文"解析同旧 task 默认，故既有用例 message / replay 不变。
    preferences: PreferenceMemory = DictPreferenceMemory()
    preferences.set_preference(QUESTION_LANGUAGE_KEY, case.language)
    if case.stocked:
        store, item_ids = build_stocked_store()
        # 与生产 assess_once 同源：候选池 = 全库（全局 KB 读），否则对照基线与生产不一致。
        items = store.all_items()
        natural = select_target(items, rng=new_rng(SEED)).item_id
        context.update(item_ids=item_ids, natural=natural, items=list(items))
        weak_target: str | None = None
        for pv in case.preset:  # 经真实 record_verdict 建前置状态（状态机不重写）
            weak_target = _resolve_target(pv.target, item_ids, natural)
            memory.record_verdict(weak_target, cast("VerdictLabel", pv.verdict))
        context["weak_target"] = weak_target
        if weak_target is not None:
            # 捕获跑 assess 前的记忆状态：case 6 靠它断言"第一次答对→观察中（仍在表内）"这一前置半。
            context["pre_state"] = memory.state_of(weak_target)
            context["pre_in_weak"] = weak_target in memory.weak_item_ids()
    else:
        store = LearningStore()
        context.update(item_ids=[], items=[])

    if provider_override is not None:
        provider: Provider = provider_override
        fake: _CountingFake | None = None
    else:
        fake = _build_assess_fake(case)
        provider = fake

    # 单轮（既有 8 用例）走 [case.answer]；多轮走 case.answers。跨轮复用同一 memory / store / 会话内
    # recently_asked 台账，每轮 rng = new_rng(SEED + round_index)——与 CLI run_quiz 的多轮驱动一致；
    # round 0 = new_rng(SEED) + 空 asked_before，故单轮路径与改动前逐字节等价（向后兼容）。
    answers = case.answers if case.answers else [case.answer]
    recently_asked: dict[str, list[str]] = {}
    all_events: list[AgentEvent] = []
    all_spans: list[Span] = []
    result: AssessmentResult | None = None
    for round_index, answer_token in enumerate(answers):
        emitter, events, trace = build_event_harness()
        result = await assess_once(
            store=store,
            provider=provider,
            responder=ScriptedResponder(answer=_resolve_answer(answer_token)),
            memory=memory,
            emitter=emitter,
            rng=new_rng(SEED + round_index),
            recently_asked=recently_asked,
            focus=case.focus,
            preferences=preferences,
        )
        all_spans.extend(trace.span_tree("run"))
        trace.close()
        all_events.extend(events)
    context["recently_asked"] = recently_asked
    return SolveResult(
        case=case,
        events=all_events,
        spans=all_spans,
        result=result,
        store=store,
        memory=memory,
        calls=fake.calls if fake is not None else 0,
        roles=fake.roles if fake is not None else [],
        context=context,
    )


async def _solve_ingest(case: Case, provider_override: Provider | None) -> SolveResult:
    store = LearningStore()
    keep_concepts = set(case.approval_keep)
    approval = ScriptedApprovalGate(keep=lambda item: item.concept in keep_concepts)

    if case.source == "boom":

        def source(_url: str) -> str:
            raise RuntimeError("抓取超时")
    else:

        def source(_url: str) -> str:
            return INGEST_RAW_CONTENT

    provider = provider_override or IngestFakeProvider(READER_JSON)
    fake = provider if isinstance(provider, IngestFakeProvider) else None
    emitter, events, trace = build_event_harness()
    result = await ingest_resource(
        INGEST_URL,
        source=source,
        provider=provider,
        store=store,
        approval=approval,
        emitter=emitter,
        max_bytes=4096,
        allowed_domains=ALLOWED_DOMAINS,
    )
    spans = trace.span_tree("run")
    trace.close()
    return SolveResult(
        case=case,
        events=events,
        spans=spans,
        result=result,
        store=store,
        memory=LearningMemory(),
        calls=fake.calls if fake is not None else 0,
        roles=fake.roles if fake is not None else [],
        context={"approved_concepts": sorted(keep_concepts)},
    )


async def solve(case: Case, *, provider_override: Provider | None = None) -> SolveResult:
    """从 ``case`` 重建确定性前置，调既有入口一次，捕获事件 + span 树 + result + 记忆 / 存储末态。

    ``provider_override`` 供硬失败测试注入会抛 ``ReplayMiss`` 的 provider——solve **不吞**任何
    provider 异常（照既有编排语义原样冒泡），由 ``run_case`` 记为硬失败。
    """
    if case.kind == "ingest":
        return await _solve_ingest(case, provider_override)
    return await _solve_assess(case, provider_override)


# --- runner + 报告 ----------------------------------------------------------------------------


@dataclass
class CaseReport:
    """一个用例的评测结果：pass/fail + 失败明细 + token 成本 + 所用 prompt 版本。"""

    case_id: str
    kind: str
    passed: bool
    failures: list[str]
    total_tokens: int
    prompt_versions: list[str]
    error: str | None = None


def _sum_tokens(events: list[AgentEvent]) -> int:
    # token 成本列：汇总每个 MODEL_ENDED payload 的 usage.total_tokens（Usage 的 computed_field）。
    total = 0
    for event in events:
        if event.type != EventType.MODEL_ENDED:
            continue
        usage = event.payload.get("usage")
        if isinstance(usage, Mapping):
            value = cast("Mapping[str, Any]", usage).get("total_tokens")
            if isinstance(value, int):
                total += value
    return total


def _prompt_versions(events: list[AgentEvent]) -> list[str]:
    # prompt 版本列：MODEL_STARTED payload 的 prompt_version（= name@digest），按首现序去重。
    versions: list[str] = []
    for event in events:
        if event.type != EventType.MODEL_STARTED:
            continue
        version = event.payload.get("prompt_version")
        if isinstance(version, str) and version not in versions:
            versions.append(version)
    return versions


async def run_case(case: Case, *, provider_override: Provider | None = None) -> CaseReport:
    """跑一个用例：校期望事件序列（YAML）+ 按 id 的规则 scorer（四族结构断言）→ CaseReport。

    solve 抛异常（``ReplayMiss`` / provider 基础设施错误 / bug）→ **硬失败**：``passed=False`` +
    捕获错误，绝不静默计为通过（决策 6）。
    """
    from grandquiz.evals.graders import GRADERS

    try:
        result = await solve(case, provider_override=provider_override)
    except Exception as exc:  # eval runner 须把任何异常记为硬失败而非冒泡中断全批
        return CaseReport(
            case_id=case.id,
            kind=case.kind,
            passed=False,
            failures=[f"solve 抛异常（硬失败，不静默通过）：{exc!r}"],
            total_tokens=0,
            prompt_versions=[],
            error=repr(exc),
        )

    failures: list[str] = []
    actual = [e.type for e in result.events]
    if actual != case.expected_events:
        failures.append(f"事件类型序列不符：期望 {case.expected_events}，实得 {actual}")
    grader = GRADERS.get(case.id)
    if grader is None:
        failures.append(f"缺少 grader：{case.id}")
    else:
        failures.extend(grader(result))
    return CaseReport(
        case_id=case.id,
        kind=case.kind,
        passed=not failures,
        failures=failures,
        total_tokens=_sum_tokens(result.events),
        prompt_versions=_prompt_versions(result.events),
    )


async def run_all() -> list[CaseReport]:
    """跑全部用例，返回按 id 排序的报告列表。"""
    return [await run_case(case) for case in load_cases()]


def render_report(reports: list[CaseReport]) -> str:
    """把报告渲染成人读文本表：case / kind / pass / tokens / prompts，附失败明细。"""
    lines = [
        f"Eval 报告：{sum(r.passed for r in reports)}/{len(reports)} 通过",
        "-" * 72,
        f"{'case':<8}{'kind':<8}{'pass':<6}{'tokens':<8}prompts",
    ]
    for r in reports:
        mark = "PASS" if r.passed else "FAIL"
        prompts = ", ".join(r.prompt_versions) if r.prompt_versions else "-"
        lines.append(f"{r.case_id:<8}{r.kind:<8}{mark:<6}{r.total_tokens:<8}{prompts}")
        for failure in r.failures:
            lines.append(f"    ✗ {failure}")
    return "\n".join(lines)


# --- HTML 导出（附加：不改 run_case / run_all 的 pass/fail，也不改文本 render_report）-----------
#
# 复用 issue 03 的 kernel.report.render_trace_html 渲染每用例详情——一个 eval 用例本身就是一条
# trace。索引页是本报告独有的跨用例汇总表（render_trace_html 只渲染单条 trace，不提供汇总），故
# 在此另建一个小内联页；per-case 详情一律复用 render_trace_html，绝不重实现 trace 渲染。

_REPORT_INDEX_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.5rem;
  font: 14px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  background: #fafafa; color: #1b1b1b;
}
h1 { font-size: 1.2rem; margin: 0 0 0.75rem; }
table.cases { border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }
table.cases th, table.cases td {
  text-align: left; padding: 0.3rem 0.7rem; border-bottom: 1px solid #e2e2e2; vertical-align: top;
}
table.cases th { color: #666; font-weight: 600; }
td.pass { color: #197f19; font-weight: 700; }
td.fail { color: #b00; font-weight: 700; }
tr.fail-detail td { color: #b00; }
a { color: inherit; }
@media (prefers-color-scheme: dark) {
  body { background: #16181d; color: #d6d6d6; }
  table.cases th, table.cases td { border-color: #262a31; }
  td.pass { color: #5fbf5f; }
  td.fail, tr.fail-detail td { color: #ff6b6b; }
}
"""


def _render_report_index(reports: list[CaseReport]) -> str:
    """跨用例汇总索引页（自包含、内联 CSS）：逐用例 pass/fail + token + prompt 版本，行链到详情页。

    纯呈现：所有动态文本（case id / prompt 版本 / 失败明细）经 ``html.escape`` 转义后注入；相对链接
    ``<a href="{id}.html">`` 指向同目录的每用例详情（各自自包含、无外部请求）。
    """
    passed = sum(r.passed for r in reports)
    rows: list[str] = []
    for r in reports:
        mark = "PASS" if r.passed else "FAIL"
        cls = "pass" if r.passed else "fail"
        prompts = ", ".join(r.prompt_versions) if r.prompt_versions else "—"
        href = html.escape(f"{r.case_id}.html", quote=True)
        rows.append(
            "<tr>"
            f'<td><a href="{href}">{html.escape(r.case_id)}</a></td>'
            f"<td>{html.escape(r.kind)}</td>"
            f'<td class="{cls}">{mark}</td>'
            f"<td>{r.total_tokens}</td>"
            f"<td>{html.escape(prompts)}</td>"
            "</tr>"
        )
        for failure in r.failures:  # 失败明细挂在该行下方（红字），便于一眼定位
            cell = f'<td colspan="4">✗ {html.escape(failure)}</td>'
            rows.append(f'<tr class="fail-detail"><td></td>{cell}</tr>')
    body = (
        f"<h1>Eval 报告 · {passed}/{len(reports)} 通过</h1>"
        '<table class="cases"><thead><tr>'
        "<th>case</th><th>kind</th><th>pass</th><th>tokens</th><th>prompts</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return (
        "<!doctype html>"
        '<html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Eval 报告</title>"
        f"<style>{_REPORT_INDEX_CSS}</style>"
        f"</head><body>{body}</body></html>"
    )


async def _solve_events_spans(case: Case) -> tuple[list[AgentEvent], list[Span]]:
    """取某用例的 events + span 森林（供 render_trace_html）；solve 抛异常 → 空（详情页仍生成）。

    与 ``run_case`` 各自独立 solve（run_case 的 pass/fail 判定保持权威、一行不改）；harness 全确定性
    且快，重复 solve 可忽略。硬失败用例（solve 冒泡）不该炸掉整份报告——降级为无 span 的详情页。
    """
    try:
        solved = await solve(case)
    except Exception:  # 报告生成对任何 solve 异常降级，绝不中断全批导出
        return [], []
    return solved.events, solved.spans


async def export_html_report(out_dir: Path) -> Path:
    """跑 eval harness → 导出可点开的自包含 HTML：索引页 + 每用例一份 render_trace_html 详情。

    多文件布局：``<out_dir>/index.html``（汇总表：逐用例 pass/fail + token + prompt 版本，链到详情）
    + ``<out_dir>/<case_id>.html``（复用 issue 03 的 ``render_trace_html`` 渲染该用例的 span 树 +
    事件流）。各文件相对链接、各自自包含、零外部请求。返回索引页路径。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    reports: list[CaseReport] = []
    for case in load_cases():
        report = await run_case(case)  # 权威 pass/fail（不改）
        events, spans = await _solve_events_spans(case)
        meta: dict[str, Any] = {
            "case_id": report.case_id,
            "kind": report.kind,
            "verdict": "PASS" if report.passed else "FAIL",
            "total_tokens": report.total_tokens,
            "prompt_versions": ", ".join(report.prompt_versions) if report.prompt_versions else "—",
            "event_count": len(events),
        }
        detail = render_trace_html(events, spans, meta=meta, title=f"用例 {report.case_id}")
        (out_dir / f"{report.case_id}.html").write_text(detail, encoding="utf-8")
        reports.append(report)
    index_path = out_dir / "index.html"
    index_path.write_text(_render_report_index(reports), encoding="utf-8")
    return index_path


def main() -> int:
    """CLI 入口（``python -m grandquiz.evals``）：跑全部用例、打印报告、返回退出码（全绿=0）。"""
    reports = asyncio.run(run_all())
    print(render_report(reports))
    return 0 if all(r.passed for r in reports) else 1
