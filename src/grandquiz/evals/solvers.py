"""Per-kind Eval solvers and their deterministic execution fixtures.

设计判断（见包 docstring）：

- **规范确定性装配**：``build_event_harness`` / ``summarize_spans`` 是 ``test_assessment`` 与
  ``test_ingest`` 里 ``_harness`` / ``_summ`` 的唯一权威版本（两测试文件 import 之、删本地重复）。
  ManualClock + 种子化 rng + trace_id="run" 保证逐字节可回放。
- **假 provider（canned JSON）**：``AssessFakeProvider`` / ``IngestFakeProvider`` 镜像两测试文件里
  的假 provider——按 role 分槽、从 messages 回抽真实证据引用、计调用次数与 role。独立于 cassette。
- **Per-kind Solver**：``IngestCase`` / ``AssessCase`` / ``ReactCase`` 各自只暴露合法配置，并由
  对应 solver 重建确定性前置；公共 ``solve`` 只分派类型并返回统一 ``SolveResult``。
- **错误语义**：本 Module 不吞 provider / Replay 异常；是否构成 suite 硬失败由 ``runner.py`` 决定。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment.engine import AssessmentResult, assess_once
from grandquiz.domain.learning.assessment.scope import ALL_SCOPE, QuizScope, SelectedScope
from grandquiz.domain.learning.assessment.selection import apply_scope, select_target
from grandquiz.domain.learning.context import learner_context_provider
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.ingest.acquisition_replay import (
    AcquisitionCassette,
    ReplayFetchSource,
    ReplaySearchProvider,
)
from grandquiz.domain.learning.ingest.fetch import ALLOW_ANY_DOMAIN, BoundedFetchSource, FetchSource
from grandquiz.domain.learning.ingest.web_search import SearchProvider
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
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.domain.learning.tools import register_learning_tools
from grandquiz.domain.learning.tools.web_search_tool import SearchToolResult, make_web_search_tool
from grandquiz.evals.case import AssessCase, Case, IngestCase, ReactCase, parse_case
from grandquiz.evals.fixture import (
    INGEST_RAW_CONTENT,
    MC_CORRECT,
    MC_WRONG,
    READER_JSON,
)
from grandquiz.evals.resources import eval_fixture_path
from grandquiz.evals.result import (
    AskedHistory,
    AssessObservation,
    BasicIngestObservation,
    ReactObservation,
    SolveResult,
    WebAcquisitionObservation,
)
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.context import ContextBuilder, Partition
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.kernel.trace import Span, TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, Usage
from grandquiz.providers.replay import Cassette, ReplayProvider

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
GROUNDED_REACT_URL = "https://example.com/agent-runtime-grounded"
GROUNDED_REACT_QUOTE = "AgentEvent 是包含 type、元数据和不透明 payload 的事件信封"
GROUNDED_REACT_CONTENT = (
    "# Agent Runtime\n\n"
    + "".join(
        f"## 运行时背景 {index}\n\n"
        f"这是用于检验渐进读取的第 {index} 段背景说明，不包含目标答案。\n\n"
        for index in range(16)
    )
    + "## 确定性 workflow\n\n核心考核由代码控制状态转移。\n\n"
    "## 事件信封\n\n"
    f"{GROUNDED_REACT_QUOTE}。trace、hook、流式输出与 eval replay 复用同一条事件流。\n\n"
    "## 恢复\n\n错误本身也是一种 AgentEvent。\n"
)

# 每个 item 一条独一无二的证据引文（互不为子串）——假 provider 据此从 messages 回抽真实证据。
ITEM_DATA: list[tuple[str, str]] = [
    ("闭包", "闭包捕获变量而非值"),
    ("变量提升", "var 声明提升到作用域顶部"),
    ("事件循环", "事件循环调度宏任务与微任务"),
]
QUOTES = {quote for _concept, quote in ITEM_DATA}

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
                payload = {
                    "question": "该知识点的核心是什么？",
                    "expected_points": [
                        {
                            "point_id": "core",
                            "description": "说明核心含义",
                            "required_claims": ["说明核心含义"],
                            "cited_evidence": quote,
                        },
                        {
                            "point_id": "boundary",
                            "description": "说明关键区分",
                            "required_claims": ["说明关键区分"],
                            "cited_evidence": quote,
                        },
                    ],
                    "reference_answer": quote,
                    "cited_evidence": [quote],
                }
        else:  # basic → 判卷
            answer_evidence_ids = re.findall(r"^- \[(v1e\d+_\d+)\]", text, flags=re.MULTILINE)
            if self._verdict == "对":
                matched, missing, diagnosis = ["core", "boundary"], [], "complete"
            elif self._verdict == "勉强":
                matched, missing, diagnosis = ["core"], ["boundary"], "missing_key_point"
            else:
                matched, missing, diagnosis = [], ["core", "boundary"], "wrong_focus"
            payload = {
                "verdict": self._verdict,
                "point_assessments": [
                    {
                        "point_id": point_id,
                        "label": "matched" if point_id in matched else "missing",
                        "answer_evidence_ids": [],
                        "claim_assessments": [
                            {
                                "claim_id": f"{point_id}.claim_1",
                                "label": "matched" if point_id in matched else "missing",
                                "answer_evidence_ids": (
                                    answer_evidence_ids if point_id in matched else []
                                ),
                                "reason": "确定性 eval claim 判定。",
                            }
                        ],
                        "reason": "确定性 eval 逐点判卷。",
                    }
                    for point_id in [*matched, *missing]
                ],
                "diagnosis": diagnosis,
                "reason": "确定性 eval 判卷反馈",
                "cited_evidence": [quote],
            }
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
            text = "\n".join(m.content for m in messages)
            answer_evidence_ids = re.findall(r"^- \[(v1e\d+_\d+)\]", text, flags=re.MULTILINE)
            return Completion(
                text=json.dumps(
                    {
                        "verdict": "对",
                        "point_assessments": [
                            {
                                "point_id": "core",
                                "label": "matched",
                                "answer_evidence_ids": [],
                                "claim_assessments": [
                                    {
                                        "claim_id": "core.claim_1",
                                        "label": "matched",
                                        "answer_evidence_ids": answer_evidence_ids,
                                        "reason": "回答覆盖了 claim。",
                                    }
                                ],
                                "reason": "回答覆盖了评分点。",
                            }
                        ],
                        "diagnosis": "complete",
                        "reason": "回答覆盖了评分点。",
                        "cited_evidence": [quote],
                    },
                    ensure_ascii=False,
                ),
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
            payload: dict[str, Any] = {
                "question": question,
                "expected_points": [
                    {
                        "point_id": "core",
                        "description": "说明核心含义",
                        "required_claims": ["说明核心含义"],
                        "cited_evidence": quote,
                    }
                ],
                "reference_answer": quote,
                "cited_evidence": [quote],
            }
        else:  # basic：恒判对（让薄弱 item 转观察中、留在薄弱优先集，复考锁定同一 item）
            text = "\n".join(m.content for m in messages)
            answer_evidence_ids = re.findall(r"^- \[(v1e\d+_\d+)\]", text, flags=re.MULTILINE)
            payload = {
                "verdict": "对",
                "point_assessments": [
                    {
                        "point_id": "core",
                        "label": "matched",
                        "answer_evidence_ids": [],
                        "claim_assessments": [
                            {
                                "claim_id": "core.claim_1",
                                "label": "matched",
                                "answer_evidence_ids": answer_evidence_ids,
                                "reason": "回答覆盖了 claim。",
                            }
                        ],
                        "reason": "回答覆盖了评分点。",
                    }
                ],
                "diagnosis": "complete",
                "reason": "回答覆盖了评分点。",
                "cited_evidence": [quote],
            }
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


def build_stocked_store() -> tuple[LearningStore, list[str]]:
    """建一个塞了 ``ITEM_DATA`` 若干 KnowledgeItem 的 store，返回 ``(store, item_ids)``。

    资源按稳定 locator 标识（``resource_id = derive_id(ASSESS_URL)``，ADR-0007）并进入全局 KB
    单池；出题语言归 Preference Memory（``_solve_assess`` 按 ``case.language`` 设偏好）。
    """
    store = LearningStore()
    resource = LearningResource.create(url=ASSESS_URL)
    store.add_resource(resource)
    items = [
        KnowledgeItem.create(
            resource_id=resource.resource_id,
            concept=concept,
            summary=f"{concept} 的一句话摘要",
            evidence=[Evidence(quote=quote)],
            confidence=0.9,
        )
        for concept, quote in ITEM_DATA
    ]
    store.add_items(items)
    return store, [item.item_id for item in items]


def build_grounded_react_store() -> tuple[LearningStore, str]:
    """为自然材料问答 ReAct eval 建一个有 current revision/tree/FTS 行为的合成资源。"""
    store = LearningStore()
    resource = LearningResource.create(url=GROUNDED_REACT_URL).model_copy(
        update={
            "raw_content": GROUNDED_REACT_CONTENT,
            "content_hash": hashlib.sha256(GROUNDED_REACT_CONTENT.encode()).hexdigest(),
            "status": "read",
            "topic": "Agent Runtime",
        }
    )
    store.replace_snapshot(resource, [])
    return store, resource.resource_id


# 资源 B 的固定 item（topic / 概念与资源 A 互不重叠）——供多资源夹具。**这些 item 在新用例里从不被
# 选中出题**（scope-honor 只考 A、empty_scope 不出题），故其证据无需进 ``QUOTES`` / 假 provider：
# B 的存在只为证明 scope 精确过滤（排除 B）与 empty_scope 的"非空库"前提。
MULTI_RESOURCE_B_URL = "https://example.com/agent-protocol"
MULTI_RESOURCE_B_TOPIC = "代理通信协议"
ITEM_DATA_B: list[tuple[str, str]] = [
    ("消息信封", "代理间消息以信封封装元数据与载荷"),
    ("能力发现", "代理通过能力清单彼此发现可调用技能"),
]


def build_multi_resource_store() -> tuple[LearningStore, dict[str, str], list[str]]:
    """建 ≥2 资源、≥2 topic 的全局 KB 夹具——**独立于 ``build_stocked_store``，绝不扰其单资源输出**。

    资源 A = 与 ``build_stocked_store`` 逐字节同源（``ASSESS_URL`` + ``ITEM_DATA``）；资源 B = 另一
    topic（代理通信协议）+ 自有 item。返回 ``(store, {"A": rid_a, "B": rid_b}, all_item_ids)``——
    ``all_item_ids`` 按 ``all_items()`` 升序，与生产 parity。供 scope-honor（scope=[A]、断言绝不
    串到 B）与 empty_scope（非空库前提）。natural 基线仍由调用方按生产 ``all_items()`` +
    ``apply_scope`` 同源计算——故加第 2 资源不改既有单资源用例的选题 / 期望（那些走
    ``build_stocked_store``）。
    """
    store, _ = build_stocked_store()  # 资源 A：与单资源夹具逐字节同源
    rid_a = LearningResource.create(url=ASSESS_URL).resource_id
    resource_b = LearningResource.create(url=MULTI_RESOURCE_B_URL).model_copy(
        update={"topic": MULTI_RESOURCE_B_TOPIC}
    )
    store.add_resource(resource_b)
    store.add_items(
        [
            KnowledgeItem.create(
                resource_id=resource_b.resource_id,
                concept=concept,
                summary=f"{concept} 的一句话摘要",
                evidence=[Evidence(quote=quote)],
                confidence=0.9,
            )
            for concept, quote in ITEM_DATA_B
        ]
    )
    all_item_ids = [item.item_id for item in store.all_items()]
    return store, {"A": rid_a, "B": resource_b.resource_id}, all_item_ids


# --- Case YAML 加载 ---------------------------------------------------------------------------

CASES_DIR = Path(__file__).parent / "cases"


def load_cases() -> list[Case]:
    """从 ``cases/*.yaml`` 加载全部用例，按 ``id`` 稳定排序。"""
    cases: list[Case] = []
    for path in CASES_DIR.glob("*.yaml"):
        try:
            cases.append(parse_case(yaml.safe_load(path.read_text(encoding="utf-8"))))
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    return sorted(cases, key=lambda c: c.id)


# --- Solver（通用适配器：case → 确定性前置 → 调既有入口一次 → 捕获事件 / trace）-----------------


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


def _build_assess_fake(case: AssessCase) -> _CountingFake:
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


async def _solve_assess(case: AssessCase, provider_override: Provider | None) -> SolveResult:
    memory = LearningMemory()
    items: list[KnowledgeItem] = []
    natural: str | None = None
    weak_target: str | None = None
    pre_state = None
    pre_in_weak: bool | None = None
    # 语言归 Preference Memory（ADR-0005）：case.language 设进 question_language 偏好、下传
    # assess_once（偏好 > 中文）。默认"中文"保持既有用例 message / replay 不变。
    preferences: PreferenceMemory = DictPreferenceMemory()
    preferences.set_preference(QUESTION_LANGUAGE_KEY, case.language)
    resource_ids: list[str] | None = None
    scope: QuizScope = ALL_SCOPE
    if case.stocked:
        if case.fixture == "multi":
            store, fixture_resources, item_ids = build_multi_resource_store()
        else:
            store, item_ids = build_stocked_store()
            fixture_resources = {}
        # scope（GKB-S7）：符号 token 解析成 exact resource_id——已知 token 走夹具映射，未知 token
        # 原样透传当"库中不存在的 id"（供 empty_scope）；空 scope → None = 全库（既有用例走此路径，
        # resource_ids 恒 None，与改动前逐字节等价）。
        resource_ids = (
            [fixture_resources.get(tok, tok) for tok in case.scope] if case.scope else None
        )
        scope = SelectedScope(resource_ids=resource_ids) if resource_ids is not None else ALL_SCOPE
        # 与生产 assess_once 同源：候选池 = 全库经 apply_scope 收窄（None → 恒等全库），natural
        # 基线亦按 scope 后的池算（scope 用例的对照基线与生产一致；None scope 下 == 全库、既有
        # 用例不变）。空命中（empty_scope）→ 不选题（选题在拒答分支后，此处置 None）。
        all_items = store.all_items()
        scoped = apply_scope(all_items, resource_ids)
        natural = select_target(scoped, rng=new_rng(SEED)).item_id if scoped else None
        items = list(all_items)
        for pv in case.preset:  # 经真实 record_verdict 建前置状态（状态机不重写）
            weak_target = _resolve_target(pv.target, item_ids, cast("str", natural))
            memory.record_verdict(weak_target, pv.verdict)
        if weak_target is not None:
            # 捕获跑 assess 前的记忆状态：case 6 靠它断言"第一次答对→观察中（仍在表内）"这一前置半。
            pre_state = memory.state_of(weak_target)
            pre_in_weak = weak_target in memory.weak_item_ids()
    else:
        store = LearningStore()

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
            scope=scope,
            question_type=case.question_type,
        )
        all_spans.extend(trace.span_tree("run"))
        trace.close()
        all_events.extend(events)
    return SolveResult(
        case=case,
        events=all_events,
        spans=all_spans,
        result=result,
        store=store,
        memory=memory,
        calls=fake.calls if fake is not None else 0,
        roles=fake.roles if fake is not None else [],
        observation=AssessObservation(
            items=tuple(items),
            natural_item_id=natural,
            selected_resource_ids=None if resource_ids is None else tuple(resource_ids),
            weak_target_item_id=weak_target,
            pre_weak_state=pre_state,
            pre_in_weak=pre_in_weak,
            recently_asked=tuple(
                AskedHistory(item_id=item_id, questions=tuple(questions))
                for item_id, questions in sorted(recently_asked.items())
            ),
        ),
    )


async def _solve_ingest(case: IngestCase, provider_override: Provider | None) -> SolveResult:
    if case.source == "web_replay":
        return await _solve_web_acquisition(case, provider_override)
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
        observation=BasicIngestObservation(),
    )


async def _solve_web_acquisition(
    case: IngestCase, provider_override: Provider | None
) -> SolveResult:
    """case16：全程只读规范化 acquisition cassette，不触公网或真实搜索服务。"""
    profile = case.acquisition_replay
    if profile is None:
        raise ValueError("web_replay case 缺 acquisition_replay profile")
    cassette = AcquisitionCassette.load(eval_fixture_path(profile.cassette))
    search = ReplaySearchProvider(
        cassette,
        adapter_name=profile.search_adapter,
        adapter_fingerprint=profile.search_fingerprint,
    )
    fetch = ReplayFetchSource(
        cassette,
        adapter_fingerprint=profile.fetch_fingerprint,
        normalization_version=profile.normalization_version,
    )
    store = LearningStore()
    keep_concepts = set(case.approval_keep)
    approval = ScriptedApprovalGate(keep=lambda item: item.concept in keep_concepts)
    provider = provider_override or IngestFakeProvider(READER_JSON)
    fake = provider if isinstance(provider, IngestFakeProvider) else None
    emitter, events, trace = build_event_harness()
    registry = ToolRegistry()
    registry.register(make_web_search_tool(provider=search))
    search_output = SearchToolResult.model_validate_json(
        await registry.dispatch(
            "web_search",
            {"query": "react hooks runtime", "limit": 3, "domains": ["example.com"]},
            ctx=ToolContext(emitter=emitter),
        )
    )
    selected_url = search_output.results[0].url
    success = await ingest_resource(
        selected_url,
        source=fetch,
        provider=provider,
        store=store,
        approval=approval,
        emitter=emitter,
        max_bytes=4096,
        allowed_domains=ALLOWED_DOMAINS,
    )
    calls_after_success = fake.calls if fake is not None else 0
    rejected_url = "https://example.com/challenge"
    rejected = await ingest_resource(
        rejected_url,
        source=fetch,
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
        result=success,
        store=store,
        memory=LearningMemory(),
        calls=fake.calls if fake is not None else 0,
        roles=fake.roles if fake is not None else [],
        observation=WebAcquisitionObservation(
            selected_url=selected_url,
            rejected_url=rejected_url,
            rejected_result=rejected,
            provider_calls_after_success=calls_after_success,
        ),
    )


def _load_react_cassette(name: str) -> ReplayProvider:
    """从包内 ``evals/fixtures/<name>`` 建 ``ReplayProvider``：从
    cassette 自带的 role→model 反推 ``model_for_role``，回放无需 ``.env``、不触网、不烧 token。
    """
    path = eval_fixture_path(name)
    raw: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    model_for_role = cast("dict[Role, str]", {e["role"]: e["model"] for e in raw.values()})
    return ReplayProvider(Cassette.load(path), model_for_role)


async def _solve_react(
    case: ReactCase,
    provider_override: Provider | None,
    *,
    search_provider_override: SearchProvider | None = None,
    fetch_source_override: BoundedFetchSource | None = None,
) -> SolveResult:
    """驱动 ``Runner.run_agent_turn``（而非 domain 函数直调）——覆盖 ReAct 决策层：LLM 会不会真的
    触发工具，而非在最终文本里编结果。装配逐字照 ``composition.build_react_runner`` 的形状（工具
    注册 + system/memory 分区），但用内存态 ``LearningStore``/``LearningMemory``（同其余用例，零
    I/O）而非生产的 SQLite 实现——两者都满足 ``register_learning_tools`` 认的 ``Store``/``Memory``
    协议，装配等价。

    ``provider_override`` 为 None 时按 ``case.cassette`` 从包内 ``evals/fixtures/`` 载入真录
    ``ReplayProvider``——react 用例**没有**"canned JSON 假件"这个选项：ReAct 决策本身就是被测行为，
    假 provider 会把它演成恒定正确、测不出真实模型是否偷懒编造。
    """

    def no_ingest_source(_url: str) -> str:
        raise AssertionError("react 用例的知识库已预先入库，不应触发 ingest")

    search_provider = None
    source: FetchSource = no_ingest_source
    if case.react_fixture == "grounded":
        store, grounded_resource_id = build_grounded_react_store()
    elif case.react_fixture == "web_acquisition":
        store = LearningStore()
        grounded_resource_id = None
        if (search_provider_override is None) != (fetch_source_override is None):
            raise ValueError("web_acquisition recording 必须同时注入 search 与 fetch")
        if search_provider_override is not None and fetch_source_override is not None:
            search_provider = search_provider_override
            source = fetch_source_override
        else:
            profile = case.acquisition_replay
            if profile is None:
                raise ValueError("web_acquisition case 缺 acquisition_replay profile")
            acquisition = AcquisitionCassette.load(eval_fixture_path(profile.cassette))
            search_provider = ReplaySearchProvider(
                acquisition,
                adapter_name=profile.search_adapter,
                adapter_fingerprint=profile.search_fingerprint,
            )
            source = ReplayFetchSource(
                acquisition,
                adapter_fingerprint=profile.fetch_fingerprint,
                normalization_version=profile.normalization_version,
            )
    else:
        store, _ = build_stocked_store()
        grounded_resource_id = None
    memory = LearningMemory()
    preferences: PreferenceMemory = DictPreferenceMemory()
    registry = ToolRegistry()

    provider = provider_override or _load_react_cassette(case.cassette)
    register_learning_tools(
        registry,
        source=source,
        provider=provider,
        store=store,
        approval=ScriptedApprovalGate(keep=lambda _item: True),
        memory=memory,
        max_bytes=4096,
        allowed_domains=(
            ALLOW_ANY_DOMAIN if case.react_fixture == "web_acquisition" else ALLOWED_DOMAINS
        ),
        responder=ScriptedResponder(answer=case.answer),
        preferences=preferences,
        quiz_seed=SEED,
        search_provider=search_provider,
    )
    prompt = load_prompt("react_system")
    context_builder = ContextBuilder(
        [
            Partition(name="system", provider=prompt.text),
            Partition(
                name="memory",
                provider=learner_context_provider(
                    store=store, memory=memory, preferences=preferences
                ),
            ),
        ]
    )
    emitter, events, trace = build_event_harness()
    runner = Runner(
        provider=provider,
        emitter=emitter,
        prompt_version=prompt.version,
        tools=registry,
        max_iterations=8,
        context_builder=context_builder,
    )
    final_outputs: list[str] = []
    for message in case.user_messages:
        final_outputs.append(await runner.run_agent_turn(message))
    spans = trace.span_tree("run")
    trace.close()
    return SolveResult(
        case=case,
        events=events,
        spans=spans,
        result=None,
        store=store,
        memory=memory,
        calls=0,
        roles=[],
        observation=ReactObservation(
            grounded_resource_id=grounded_resource_id,
            final_outputs=tuple(final_outputs),
            full_document_chars=(
                len(GROUNDED_REACT_CONTENT) if grounded_resource_id is not None else 0
            ),
        ),
    )


async def solve(
    case: Case,
    *,
    provider_override: Provider | None = None,
    search_provider_override: SearchProvider | None = None,
    fetch_source_override: BoundedFetchSource | None = None,
) -> SolveResult:
    """从 ``case`` 重建确定性前置，调既有入口一次，捕获事件 + span 树 + result + 记忆 / 存储末态。

    ``provider_override`` 供硬失败测试注入会抛 ``ReplayMiss`` 的 provider——solve **不吞**任何
    provider 异常（照既有编排语义原样冒泡），由 ``run_case`` 记为硬失败。
    """
    if isinstance(case, IngestCase):
        return await _solve_ingest(case, provider_override)
    if isinstance(case, ReactCase):
        return await _solve_react(
            case,
            provider_override,
            search_provider_override=search_provider_override,
            fetch_source_override=fetch_source_override,
        )
    return await _solve_assess(case, provider_override)
