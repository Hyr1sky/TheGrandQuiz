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
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

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
    LearningTask,
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.selection import select_target
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
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
        ]
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

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
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

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        self.roles.append(role)
        return Completion(text=self.text, usage=Usage(prompt_tokens=7, completion_tokens=3))


def build_stocked_store() -> tuple[LearningStore, LearningTask, list[str]]:
    """建一个塞了 ``ITEM_DATA`` 若干 KnowledgeItem 的 store，返回 ``(store, task, item_ids)``。"""
    store = LearningStore()
    task = LearningTask.create("React")
    resource = LearningResource.create(task_id=task.task_id, url=ASSESS_URL)
    store.add_task(task)
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
    return store, task, [item.item_id for item in items]


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
        return Case(
            id=case_id,
            kind="assess",
            expected_events=expected,
            stocked=bool(setup.get("stocked", True)),
            preset=preset,
            answer=str(setup.get("answer", "我的作答")),
            verdict=str(setup.get("verdict", "对")),
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
    return {"mc_correct": MC_CORRECT, "mc_wrong": MC_WRONG}.get(token, token)


def _resolve_target(selector: str, item_ids: list[str], natural: str) -> str:
    if selector == "non_natural":
        return next(i for i in item_ids if i != natural)
    if selector.startswith("index:"):
        return item_ids[int(selector.split(":", 1)[1])]
    raise ValueError(f"未知 target 选择器：{selector}")


async def _solve_assess(case: Case, provider_override: Provider | None) -> SolveResult:
    memory = LearningMemory()
    context: dict[str, Any] = {}
    if case.stocked:
        store, task, item_ids = build_stocked_store()
        items = store.items_for_task(task.task_id)
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
        task = LearningTask.create("React")
        context.update(item_ids=[], items=[])

    provider = provider_override or AssessFakeProvider(case.verdict)
    fake = provider if isinstance(provider, AssessFakeProvider) else None
    emitter, events, trace = build_event_harness()
    result = await assess_once(
        task,
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer=_resolve_answer(case.answer)),
        memory=memory,
        emitter=emitter,
        rng=new_rng(SEED),
    )
    spans = trace.span_tree("run")
    trace.close()
    return SolveResult(
        case=case,
        events=events,
        spans=spans,
        result=result,
        store=store,
        memory=memory,
        calls=fake.calls if fake is not None else 0,
        roles=fake.roles if fake is not None else [],
        context=context,
    )


async def _solve_ingest(case: Case, provider_override: Provider | None) -> SolveResult:
    store = LearningStore()
    task = LearningTask.create("React")
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
        task,
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


def main() -> int:
    """CLI 入口（``python -m grandquiz.evals``）：跑全部用例、打印报告、返回退出码（全绿=0）。"""
    reports = asyncio.run(run_all())
    print(render_report(reports))
    return 0 if all(r.passed for r in reports) else 1
