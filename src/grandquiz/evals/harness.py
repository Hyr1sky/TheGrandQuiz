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
import hashlib
import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import yaml

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment.engine import AssessmentResult, assess_once
from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.scope import ALL_SCOPE, QuizScope, SelectedScope
from grandquiz.domain.learning.assessment.selection import Focus, apply_scope, select_target
from grandquiz.domain.learning.context import learner_context_provider
from grandquiz.domain.learning.ingest import IngestResult, ingest_resource
from grandquiz.domain.learning.ingest.acquisition_replay import (
    AcquisitionCassette,
    ReplayFetchSource,
    ReplaySearchProvider,
)
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
from grandquiz.evals.quality import QualityEvaluation, QualityRequest
from grandquiz.evals.quality_calibration import CalibratedQualitySuite
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.context import ContextBuilder, Partition
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.report import render_trace_html
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.kernel.trace import Span, TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, Usage
from grandquiz.providers.replay import Cassette, ReplayProvider

_QUALITY_CASSETTE = Path("tests/fixtures/eval_quality_grounded_answer.cassette.json")

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
INGEST_RAW_CONTENT = "React hooks 深读材料：q1、q2、q3"
READER_JSON = json.dumps(
    {
        "topic": "JavaScript 核心机制",
        "candidates": [
            {
                "concept": "闭包",
                "summary": "s1",
                "evidence": [
                    {
                        "node_key": "n000001",
                        "start_offset": INGEST_RAW_CONTENT.index("q1"),
                        "end_offset": INGEST_RAW_CONTENT.index("q1") + 2,
                        "quote": "q1",
                    }
                ],
                "confidence": 0.9,
            },
            {
                "concept": "变量提升",
                "summary": "s2",
                "evidence": [
                    {
                        "node_key": "n000001",
                        "start_offset": INGEST_RAW_CONTENT.index("q2"),
                        "end_offset": INGEST_RAW_CONTENT.index("q2") + 2,
                        "quote": "q2",
                    }
                ],
                "confidence": 0.8,
            },
            {
                "concept": "事件循环",
                "summary": "s3",
                "evidence": [
                    {
                        "node_key": "n000001",
                        "start_offset": INGEST_RAW_CONTENT.index("q3"),
                        "end_offset": INGEST_RAW_CONTENT.index("q3") + 2,
                        "quote": "q3",
                    }
                ],
                "confidence": 0.7,
            },
        ],
    },
    ensure_ascii=False,
)
INGEST_APPROVED_CONCEPTS = ["闭包", "事件循环"]
INGEST_CANDIDATE_COUNT = 3


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


@dataclass(frozen=True)
class QualityProfile:
    """一个用例显式选择的预注册 Tier-2 rubric 与最小参考证据。"""

    rubric_id: str
    reference: str


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
    kind: Literal["ingest", "assess", "react"]
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
    # 多资源夹具选择（GKB-S7）：single = build_stocked_store（默认，既有用例逐字节不变）；
    # multi = build_multi_resource_store（≥2 资源，供 scope 用例）。
    fixture: Literal["single", "multi"] = "single"
    # 目录式 scope（GKB-S7）：符号 token 列表（"A"/"B" 走多资源夹具映射，未知 token 原样当"库中
    # 不存在的 resource_id"，供 empty_scope 用例）；空列表 = 无 scope = 全库（resource_ids=None）。
    # 既有用例不填 → resource_ids 恒 None、与改动前字节等价。
    scope: list[str] = field(default_factory=_empty_strs)
    # 用户显式题型意图短语（GKB-S5/S7）：透传 assess_once；None = 走记忆状态自适应路由
    # （既有用例不变）。
    question_type: str | None = None
    # ingest 专属
    source: Literal["ok", "boom", "web_replay"] = "ok"
    approval_keep: list[str] = field(default_factory=_empty_strs)
    # react 专属（驱动 Runner.run_agent_turn 而非 domain 函数直调——覆盖 ReAct 决策层，Tier-1 harness
    # 此前的盲区）：user_messages 逐条喂给 run_agent_turn；cassette 是真机录制的响应库文件名（相对
    # tests/fixtures/），react 用例**必须**提供真录 cassette——ReAct 决策本身就是被测行为，用假
    # provider 演会失去测试意义。answer 复用给 start_quiz 内部逐题作答的 ScriptedResponder。
    user_messages: list[str] = field(default_factory=_empty_strs)
    cassette: str | None = None
    react_fixture: Literal["quiz", "grounded"] = "quiz"
    quality: QualityProfile | None = None


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
        raw_fixture = str(setup.get("fixture", "single"))
        fixture: Literal["single", "multi"] = (
            raw_fixture if raw_fixture in ("single", "multi") else "single"
        )
        raw_qt = setup.get("question_type")
        question_type = str(raw_qt) if raw_qt is not None else None
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
            fixture=fixture,
            scope=[str(s) for s in setup.get("scope", [])],
            question_type=question_type,
        )
    if str(raw["kind"]) == "react":
        raw_fixture = str(setup.get("fixture", "quiz"))
        react_fixture: Literal["quiz", "grounded"] = (
            "grounded" if raw_fixture == "grounded" else "quiz"
        )
        raw_quality: Any = setup.get("quality")
        quality_mapping = (
            cast("Mapping[str, Any]", raw_quality) if isinstance(raw_quality, Mapping) else None
        )
        quality = (
            QualityProfile(
                rubric_id=str(quality_mapping["rubric_id"]),
                reference=str(quality_mapping["reference"]),
            )
            if quality_mapping is not None
            else None
        )
        return Case(
            id=case_id,
            kind="react",
            expected_events=expected,
            answer=str(setup.get("answer", "我的作答")),
            user_messages=[str(m) for m in setup.get("user_messages", [])],
            cassette=str(setup["cassette"]),
            react_fixture=react_fixture,
            quality=quality,
        )
    raw_source = str(setup.get("source", "ok"))
    src: Literal["ok", "boom", "web_replay"] = (
        "web_replay" if raw_source == "web_replay" else "boom" if raw_source == "boom" else "ok"
    )
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
        context.update(
            item_ids=item_ids,
            natural=natural,
            items=list(all_items),
            resource_ids=resource_ids,
            scope=scope,
        )
        weak_target: str | None = None
        for pv in case.preset:  # 经真实 record_verdict 建前置状态（状态机不重写）
            weak_target = _resolve_target(pv.target, item_ids, cast("str", natural))
            memory.record_verdict(weak_target, cast("VerdictLabel", pv.verdict))
        context["weak_target"] = weak_target
        if weak_target is not None:
            # 捕获跑 assess 前的记忆状态：case 6 靠它断言"第一次答对→观察中（仍在表内）"这一前置半。
            context["pre_state"] = memory.state_of(weak_target)
            context["pre_in_weak"] = weak_target in memory.weak_item_ids()
    else:
        store = LearningStore()
        context.update(item_ids=[], items=[], resource_ids=None)

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
        context={"approved_concepts": sorted(keep_concepts)},
    )


async def _solve_web_acquisition(case: Case, provider_override: Provider | None) -> SolveResult:
    """case16：全程只读规范化 acquisition cassette，不触公网或真实搜索服务。"""
    cassette = AcquisitionCassette.load(
        Path("tests/fixtures/eval_case16_web_acquisition.cassette.json")
    )
    fingerprint = "eval:synthetic-web-v1"
    search = ReplaySearchProvider(
        cassette,
        adapter_name="synthetic_search",
        adapter_fingerprint=fingerprint,
    )
    fetch = ReplayFetchSource(
        cassette,
        adapter_fingerprint=fingerprint,
        normalization_version="trafilatura:2.1.0/web-v1",
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
        context={
            "selected_url": selected_url,
            "rejected_url": rejected_url,
            "rejected_result": rejected,
            "calls_after_success": calls_after_success,
        },
    )


def _load_react_cassette(name: str) -> ReplayProvider:
    """从 ``tests/fixtures/<name>`` 建 ``ReplayProvider``（同 test_assess_replay 的复原套路）：从
    cassette 自带的 role→model 反推 ``model_for_role``，回放无需 ``.env``、不触网、不烧 token。
    """
    path = Path("tests/fixtures") / name
    raw: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    model_for_role = cast("dict[Role, str]", {e["role"]: e["model"] for e in raw.values()})
    return ReplayProvider(Cassette.load(path), model_for_role)


async def _solve_react(case: Case, provider_override: Provider | None) -> SolveResult:
    """驱动 ``Runner.run_agent_turn``（而非 domain 函数直调）——覆盖 ReAct 决策层：LLM 会不会真的
    触发工具，而非在最终文本里编结果。装配逐字照 ``composition.build_react_runner`` 的形状（工具
    注册 + system/memory 分区），但用内存态 ``LearningStore``/``LearningMemory``（同其余用例，零
    I/O）而非生产的 SQLite 实现——两者都满足 ``register_learning_tools`` 认的 ``Store``/``Memory``
    协议，装配等价。

    ``provider_override`` 为 None 时按 ``case.cassette`` 从 ``tests/fixtures/`` 载入真录
    ``ReplayProvider``——react 用例**没有**"canned JSON 假件"这个选项：ReAct 决策本身就是被测行为，
    假 provider 会把它演成恒定正确、测不出真实模型是否偷懒编造。
    """
    if case.react_fixture == "grounded":
        store, grounded_resource_id = build_grounded_react_store()
    else:
        store, _ = build_stocked_store()
        grounded_resource_id = None
    memory = LearningMemory()
    preferences: PreferenceMemory = DictPreferenceMemory()
    registry = ToolRegistry()

    def source(_url: str) -> str:
        raise AssertionError("react 用例的知识库已预先入库，不应触发 ingest")

    provider = provider_override or _load_react_cassette(cast("str", case.cassette))
    register_learning_tools(
        registry,
        source=source,
        provider=provider,
        store=store,
        approval=ScriptedApprovalGate(keep=lambda _item: True),
        memory=memory,
        max_bytes=4096,
        allowed_domains=ALLOWED_DOMAINS,
        responder=ScriptedResponder(answer=case.answer),
        preferences=preferences,
        quiz_seed=SEED,
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
        context={
            "resource_id": grounded_resource_id,
            "final_outputs": final_outputs,
            "full_document_chars": (
                len(GROUNDED_REACT_CONTENT) if grounded_resource_id is not None else 0
            ),
        },
    )


async def solve(case: Case, *, provider_override: Provider | None = None) -> SolveResult:
    """从 ``case`` 重建确定性前置，调既有入口一次，捕获事件 + span 树 + result + 记忆 / 存储末态。

    ``provider_override`` 供硬失败测试注入会抛 ``ReplayMiss`` 的 provider——solve **不吞**任何
    provider 异常（照既有编排语义原样冒泡），由 ``run_case`` 记为硬失败。
    """
    if case.kind == "ingest":
        return await _solve_ingest(case, provider_override)
    if case.kind == "react":
        return await _solve_react(case, provider_override)
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
    rule_passed: bool = False
    quality_passed: bool | None = None
    quality_rubric_id: str | None = None
    judge_tokens: int = 0
    quality_evaluation: QualityEvaluation | None = None
    subject_events: list[AgentEvent] = field(default_factory=lambda: list[AgentEvent]())
    subject_spans: list[Span] = field(default_factory=lambda: list[Span]())
    quality_events: list[AgentEvent] = field(default_factory=lambda: list[AgentEvent]())
    quality_spans: list[Span] = field(default_factory=lambda: list[Span]())

    @property
    def execution_tokens(self) -> int:
        """兼容旧 ``total_tokens`` 名称，同时明确它只属于被测 workflow。"""
        return self.total_tokens

    @property
    def judge_prompt_versions(self) -> list[str]:
        return _prompt_versions(self.quality_events)


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


async def run_case(
    case: Case,
    *,
    provider_override: Provider | None = None,
    quality_suite: CalibratedQualitySuite | None = None,
    quality_unavailable_reason: str | None = None,
) -> CaseReport:
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
            rule_passed=False,
            quality_rubric_id=case.quality.rubric_id if case.quality is not None else None,
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
    rule_passed = not failures
    quality_evaluation: QualityEvaluation | None = None
    quality_passed: bool | None = None
    quality_events: list[AgentEvent] = []
    quality_spans: list[Span] = []
    if case.quality is not None and quality_suite is None:
        suffix = f"：{quality_unavailable_reason}" if quality_unavailable_reason else ""
        failures.append(f"Tier-2 缺少已校准 QualitySuite，不能退化为仅运行规则门{suffix}")
        quality_passed = False
    elif case.quality is not None and quality_suite is not None:
        final_outputs = cast("list[str]", result.context.get("final_outputs", []))
        if not case.user_messages or not final_outputs:
            failures.append("Tier-2 缺少用户问题或最终用户可见回答")
            quality_passed = False
        else:
            quality_emitter, quality_events, quality_trace = build_event_harness()
            try:
                quality_evaluation = await quality_suite.evaluate(
                    QualityRequest(
                        rubric_id=case.quality.rubric_id,
                        question=case.user_messages[-1],
                        candidate=final_outputs[-1],
                        reference=case.quality.reference,
                    ),
                    emitter=quality_emitter,
                )
            except Exception as exc:
                failures.append(f"Tier-2 judge 抛异常（质量硬失败）：{exc!r}")
                quality_passed = False
            finally:
                quality_spans = quality_trace.span_tree("run")
                quality_trace.close()
            if quality_evaluation is not None:
                quality_passed = quality_evaluation.passed
            if quality_evaluation is not None and not quality_passed:
                failures.extend(
                    f"Tier-2 {criterion.criterion_id}={criterion.score}：{criterion.rationale}"
                    for criterion in quality_evaluation.criteria
                    if criterion.score < 3
                )
    return CaseReport(
        case_id=case.id,
        kind=case.kind,
        passed=rule_passed and quality_passed is not False,
        failures=failures,
        total_tokens=_sum_tokens(result.events),
        prompt_versions=_prompt_versions(result.events),
        rule_passed=rule_passed,
        quality_passed=quality_passed,
        quality_rubric_id=case.quality.rubric_id if case.quality is not None else None,
        judge_tokens=(
            quality_evaluation.usage.total_tokens if quality_evaluation is not None else 0
        ),
        quality_evaluation=quality_evaluation,
        subject_events=result.events,
        subject_spans=result.spans,
        quality_events=quality_events,
        quality_spans=quality_spans,
    )


def _load_quality_cassette() -> ReplayProvider:
    raw: dict[str, dict[str, str]] = json.loads(_QUALITY_CASSETTE.read_text(encoding="utf-8"))
    model_for_role = cast(
        "dict[Role, str]", {entry["role"]: entry["model"] for entry in raw.values()}
    )
    return ReplayProvider(Cassette.load(_QUALITY_CASSETTE), model_for_role)


async def run_all(*, quality_provider_override: Provider | None = None) -> list[CaseReport]:
    """跑全部用例；Tier-2 先校准一次，默认仅从固定 cassette 离线回放。"""
    suite: CalibratedQualitySuite | None = None
    unavailable_reason: str | None = None
    try:
        provider = quality_provider_override or _load_quality_cassette()
        suite = await CalibratedQualitySuite.create(provider=provider)
    except Exception as exc:
        unavailable_reason = repr(exc)
    return [
        await run_case(
            case,
            quality_suite=suite,
            quality_unavailable_reason=unavailable_reason,
        )
        for case in load_cases()
    ]


def render_report(reports: list[CaseReport]) -> str:
    """把报告渲染成人读文本表：双 Tier verdict、分列成本与失败明细。"""
    lines = [
        f"Eval 报告：{sum(r.passed for r in reports)}/{len(reports)} 通过",
        "-" * 88,
        f"{'case':<8}{'kind':<8}{'all':<6}{'rule':<6}{'quality':<9}{'exec':<8}{'judge':<8}rubric",
    ]
    for r in reports:
        mark = "PASS" if r.passed else "FAIL"
        rule = "PASS" if r.rule_passed else "FAIL"
        quality = "N/A" if r.quality_passed is None else "PASS" if r.quality_passed else "FAIL"
        rubric = r.quality_rubric_id or "-"
        lines.append(
            f"{r.case_id:<8}{r.kind:<8}{mark:<6}{rule:<6}{quality:<9}"
            f"{r.execution_tokens:<8}{r.judge_tokens:<8}{rubric}"
        )
        for failure in r.failures:
            lines.append(f"    ✗ {failure}")
    return "\n".join(lines)


# --- HTML 导出（附加：不改 run_case / run_all 的 pass/fail，也不改文本 render_report）-----------
#
# 复用 issue 03 的 kernel.report.render_trace_html 渲染每用例详情——一个 eval 用例本身就是一条
# trace。索引页是本报告独有的跨用例汇总表（render_trace_html 只渲染单条 trace，不提供汇总），故
# 在此另建一个小内联页；per-case 详情一律复用 render_trace_html，绝不重实现 trace 渲染。
#
# v1 静态增强（跨用例排序/筛选 + 汇总条）：仍是零依赖纯前端——排序/筛选用一段内联原生 JS
# （``_REPORT_INDEX_JS``），不加构建步骤、不引 CDN、不装 JS 框架；唯一自包含边界的变化是索引页现在
# 含一个**内联**（非外链）``<script>``，测试的自包含断言相应改为"禁止外部脚本/样式表"而非"零 JS"
# （见 ``tests/test_cli_report.py::_assert_self_contained``）。per-case 详情页（render_trace_html）
# 不受影响，仍是纯 ``<details>``、零 JS。

_REPORT_INDEX_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.5rem;
  font: 14px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  background: #fafafa; color: #1b1b1b;
}
h1 { font-size: 1.2rem; margin: 0 0 0.75rem; }
.summary { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 0 0 1rem; }
.summary .stat {
  border: 1px solid #e2e2e2; border-radius: 6px; padding: 0.35rem 0.9rem; min-width: 5rem;
}
.summary .stat .n { display: block; font-size: 1.15rem; font-weight: 700; }
.summary .stat .l { color: #666; font-size: 0.78em; }
.summary .stat.ok .n { color: #197f19; }
.summary .stat.bad .n { color: #b00; }
.controls { margin: 0 0 0.75rem; }
.controls input[type="search"] {
  font: inherit; padding: 0.3rem 0.6rem; width: 100%; max-width: 22rem;
  border: 1px solid #ccc; border-radius: 4px; background: inherit; color: inherit;
}
.controls select {
  font: inherit; padding: 0.3rem 0.6rem; margin-left: 0.5rem;
  border: 1px solid #ccc; border-radius: 4px; background: inherit; color: inherit;
}
table.cases { border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }
table.cases th, table.cases td {
  text-align: left; padding: 0.3rem 0.7rem; border-bottom: 1px solid #e2e2e2; vertical-align: top;
}
table.cases th { color: #666; font-weight: 600; }
table.cases th[data-sort-key] { cursor: pointer; user-select: none; }
table.cases th[data-sort-key]:hover { color: #1b1b1b; }
table.cases th.sort-asc::after { content: "\\2009\\25B4"; }
table.cases th.sort-desc::after { content: "\\2009\\25BE"; }
td.pass { color: #197f19; font-weight: 700; }
td.fail { color: #b00; font-weight: 700; }
tr.fail-detail td { color: #b00; }
tr.case-row.hidden, tr.fail-detail.hidden { display: none; }
a { color: inherit; }
@media (prefers-color-scheme: dark) {
  body { background: #16181d; color: #d6d6d6; }
  .summary .stat { border-color: #262a31; }
  .summary .stat .l { color: #9a9a9a; }
  .summary .stat.ok .n { color: #5fbf5f; }
  .summary .stat.bad .n { color: #ff6b6b; }
  .controls input[type="search"] { border-color: #3a3f47; }
  .controls select { border-color: #3a3f47; }
  table.cases th, table.cases td { border-color: #262a31; }
  table.cases th[data-sort-key]:hover { color: #eee; }
  td.pass { color: #5fbf5f; }
  td.fail, tr.fail-detail td { color: #ff6b6b; }
}
"""

# 索引页排序/筛选交互：纯内联 vanilla JS（无框架、无构建步骤、无外部脚本）。失败明细行
# （``tr.fail-detail``）没有独立排序键，靠与其所属用例行相同的 ``data-id`` 分组——排序 /
# 筛选按"组"（用例行 + 其后紧跟的失败明细行）整体移动，绝不拆散一条用例的失败说明。
_REPORT_INDEX_JS = """
(function () {
  var table = document.querySelector("table.cases");
  if (!table) return;
  var tbody = table.tBodies[0];
  var headers = Array.prototype.slice.call(table.querySelectorAll("th[data-sort-key]"));
  var filterInput = document.getElementById("case-filter");
  var statusFilter = document.getElementById("status-filter");
  var state = { key: null, dir: 1 };

  function rowGroups() {
    var rows = Array.prototype.slice.call(tbody.rows);
    var groups = [];
    var current = null;
    rows.forEach(function (row) {
      if (row.classList.contains("case-row")) {
        current = { key: row, rows: [row] };
        groups.push(current);
      } else if (current) {
        current.rows.push(row);
      }
    });
    return groups;
  }

  function sortValue(row, key) {
    if (key === "tokens") return parseInt(row.dataset.tokens, 10) || 0;
    if (key === "pass") return row.dataset.pass === "1" ? 1 : 0;
    return (row.dataset[key] || "").toLowerCase();
  }

  function applySort(key) {
    var dir = state.key === key ? -state.dir : 1;
    state = { key: key, dir: dir };
    var groups = rowGroups();
    groups.sort(function (a, b) {
      var va = sortValue(a.key, key);
      var vb = sortValue(b.key, key);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
    groups.forEach(function (g) {
      g.rows.forEach(function (r) { tbody.appendChild(r); });
    });
    headers.forEach(function (h) { h.classList.remove("sort-asc", "sort-desc"); });
    var active = table.querySelector('th[data-sort-key="' + key + '"]');
    if (active) active.classList.add(dir === 1 ? "sort-asc" : "sort-desc");
  }

  headers.forEach(function (h) {
    h.addEventListener("click", function () { applySort(h.dataset.sortKey); });
  });

  function applyFilters() {
    var q = filterInput ? filterInput.value.trim().toLowerCase() : "";
    var status = statusFilter ? statusFilter.value : "all";
    rowGroups().forEach(function (g) {
      var haystack = (g.key.dataset.id + " " + g.key.dataset.kind).toLowerCase();
      var textMatch = q === "" || haystack.indexOf(q) !== -1;
      var statusMatch = status === "all" ||
        (status === "pass" && g.key.dataset.pass === "1") ||
        (status === "rule-fail" && g.key.dataset.rule === "fail") ||
        (status === "quality-fail" && g.key.dataset.quality === "fail");
      g.rows.forEach(function (r) { r.classList.toggle("hidden", !(textMatch && statusMatch)); });
    });
  }
  if (filterInput) filterInput.addEventListener("input", applyFilters);
  if (statusFilter) statusFilter.addEventListener("change", applyFilters);
})();
"""


def _render_summary(reports: list[CaseReport]) -> str:
    """顶部紧凑统计条：通过 / 失败数 + 全部用例 token 总量（复用 ``_REPORT_INDEX_CSS`` 呈现美学）。

    纯呈现、无动态文本注入风险（三个数字均是内部计算的 int，无需转义）。
    """
    passed = sum(r.passed for r in reports)
    failed = len(reports) - passed
    execution_tokens = sum(r.execution_tokens for r in reports)
    judge_tokens = sum(r.judge_tokens for r in reports)
    failed_cls = "bad" if failed else "ok"
    return (
        '<div class="summary">'
        '<div class="stat ok"><span class="n">'
        f'{passed}</span><span class="l">passed</span></div>'
        f'<div class="stat {failed_cls}"><span class="n">'
        f'{failed}</span><span class="l">failed</span></div>'
        '<div class="stat"><span class="n">'
        f'{execution_tokens}</span><span class="l">execution tokens</span></div>'
        '<div class="stat"><span class="n">'
        f'{judge_tokens}</span><span class="l">judge tokens</span></div>'
        "</div>"
    )


def _render_report_index(reports: list[CaseReport]) -> str:
    """跨用例汇总索引页（自包含、内联 CSS + 内联 JS）：逐用例 pass/fail + token + prompt 版本，
    行链到详情页；附顶部通过/失败/token 汇总条，表头可点击排序（case id / kind / pass-fail /
    tokens），文本框可客户端筛选可见行。

    纯呈现：所有动态文本（case id / prompt 版本 / 失败明细）经 ``html.escape`` 转义后注入（含用作
    ``data-*`` 属性值时）；相对链接 ``<a href="{id}.html">`` 指向同目录的每用例详情（各自自包含、
    无外部请求）。排序/筛选是纯客户端行为，不改变 ``reports`` 本身、不影响 pass/fail 判定。
    """
    passed = sum(r.passed for r in reports)
    rows: list[str] = []
    for r in reports:
        mark = "PASS" if r.passed else "FAIL"
        cls = "pass" if r.passed else "fail"
        rule_mark = "PASS" if r.rule_passed else "FAIL"
        rule_cls = "pass" if r.rule_passed else "fail"
        if r.quality_passed is None:
            quality_mark = "N/A"
            quality_cls = ""
            quality_data = "na"
        else:
            quality_mark = "PASS" if r.quality_passed else "FAIL"
            quality_cls = "pass" if r.quality_passed else "fail"
            quality_data = "pass" if r.quality_passed else "fail"
        rubric = r.quality_rubric_id or "—"
        prompts = ", ".join(r.prompt_versions) if r.prompt_versions else "—"
        judge_prompts = ", ".join(r.judge_prompt_versions) if r.judge_prompt_versions else "—"
        href = html.escape(f"{r.case_id}.html", quote=True)
        case_id_attr = html.escape(r.case_id, quote=True)
        kind_attr = html.escape(r.kind, quote=True)
        rows.append(
            '<tr class="case-row" '
            f'data-id="{case_id_attr}" data-kind="{kind_attr}" '
            f'data-pass="{1 if r.passed else 0}" data-rule="{rule_cls}" '
            f'data-quality="{quality_data}" data-tokens="{r.total_tokens}">'
            f'<td><a href="{href}">{html.escape(r.case_id)}</a></td>'
            f"<td>{html.escape(r.kind)}</td>"
            f'<td class="{cls}">{mark}</td>'
            f'<td class="{rule_cls}">{rule_mark}</td>'
            f'<td class="{quality_cls}">{quality_mark}</td>'
            f"<td>{r.total_tokens}</td>"
            f"<td>{r.judge_tokens}</td>"
            f"<td>{html.escape(rubric)}</td>"
            f"<td>{html.escape(prompts)}</td>"
            f"<td>{html.escape(judge_prompts)}</td>"
            "</tr>"
        )
        for failure in r.failures:  # 失败明细挂在该行下方（红字）；同 data-id 供 JS 分组整体移动
            cell = f'<td colspan="9">✗ {html.escape(failure)}</td>'
            rows.append(f'<tr class="fail-detail" data-id="{case_id_attr}"><td></td>{cell}</tr>')
    body = (
        f"<h1>Eval 报告 · {passed}/{len(reports)} 通过</h1>"
        f"{_render_summary(reports)}"
        '<div class="controls">'
        '<input type="search" id="case-filter" placeholder="筛选 case id / kind…" '
        'aria-label="筛选用例">'
        '<select id="status-filter" aria-label="筛选状态">'
        '<option value="all">全部状态</option>'
        '<option value="pass">全部通过</option>'
        '<option value="rule-fail">Rule 失败</option>'
        '<option value="quality-fail">Quality 失败</option>'
        "</select>"
        "</div>"
        '<table class="cases"><thead><tr>'
        '<th data-sort-key="id">case</th>'
        '<th data-sort-key="kind">kind</th>'
        '<th data-sort-key="pass">pass</th>'
        "<th>Rule</th>"
        "<th>Quality</th>"
        '<th data-sort-key="tokens">execution tokens</th>'
        "<th>judge tokens</th>"
        "<th>rubric</th>"
        "<th>subject prompts</th>"
        "<th>judge prompts</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return (
        "<!doctype html>"
        '<html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Eval 报告</title>"
        f"<style>{_REPORT_INDEX_CSS}</style>"
        f"</head><body>{body}"
        f"<script>{_REPORT_INDEX_JS}</script>"
        "</body></html>"
    )


def _quality_detail_section(report: CaseReport) -> str:
    """把结构化质量判定附到 subject 详情；judge 事件树仍交给 trace renderer 单独渲染。"""
    evaluation = report.quality_evaluation
    if evaluation is None:
        return ""
    rows: list[str] = []
    for criterion in evaluation.criteria:
        rows.append(
            "<tr>"
            f"<td>{html.escape(criterion.criterion_id)}</td>"
            f"<td>{criterion.score}</td>"
            f"<td>{html.escape(criterion.rationale)}</td>"
            f"<td>{html.escape(criterion.candidate_evidence)}</td>"
            f"<td>{html.escape(criterion.reference_evidence)}</td>"
            "</tr>"
        )
    return (
        '<section class="quality-evaluation">'
        "<h2>Tier-2 Quality</h2>"
        '<div class="meta">'
        f'<span class="kv"><span class="k">rubric</span> '
        f'<span class="v">{html.escape(evaluation.rubric_id)}</span></span>'
        f'<span class="kv"><span class="k">prompt</span> '
        f'<span class="v">{html.escape(evaluation.prompt_version)}</span></span>'
        f'<span class="kv"><span class="k">judge tokens</span> '
        f'<span class="v">{evaluation.usage.total_tokens}</span></span>'
        '</div><table class="events"><thead><tr>'
        "<th>criterion</th><th>score</th><th>rationale</th>"
        "<th>candidate evidence</th><th>reference evidence</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        f"<p>{html.escape(evaluation.overall_rationale)}</p>"
        f'<p><a href="{html.escape(report.case_id, quote=True)}-quality.html">'
        "查看独立 judge trace</a></p></section>"
    )


def _append_before_body(document: str, fragment: str) -> str:
    return document.replace("</body>", f"{fragment}</body>", 1)


async def export_html_report(out_dir: Path) -> Path:
    """跑 eval harness → 导出可点开的自包含 HTML：索引页 + 每用例一份 render_trace_html 详情。

    多文件布局：``<out_dir>/index.html``（汇总表：逐用例 pass/fail + token + prompt 版本，链到详情）
    + ``<out_dir>/<case_id>.html``（复用 issue 03 的 ``render_trace_html`` 渲染该用例的 span 树 +
    事件流）。各文件相对链接、各自自包含、零外部请求。返回索引页路径。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = await run_all()
    for report in reports:
        meta: dict[str, Any] = {
            "case_id": report.case_id,
            "kind": report.kind,
            "verdict": "PASS" if report.passed else "FAIL",
            "rule": "PASS" if report.rule_passed else "FAIL",
            "quality": (
                "N/A"
                if report.quality_passed is None
                else "PASS"
                if report.quality_passed
                else "FAIL"
            ),
            "execution_tokens": report.total_tokens,
            "judge_tokens": report.judge_tokens,
            "rubric": report.quality_rubric_id or "—",
            "prompt_versions": ", ".join(report.prompt_versions) if report.prompt_versions else "—",
            "event_count": len(report.subject_events),
        }
        detail = render_trace_html(
            report.subject_events,
            report.subject_spans,
            meta=meta,
            title=f"用例 {report.case_id}",
        )
        detail = _append_before_body(detail, _quality_detail_section(report))
        (out_dir / f"{report.case_id}.html").write_text(detail, encoding="utf-8")
        if report.quality_events:
            quality_trace = render_trace_html(
                report.quality_events,
                report.quality_spans,
                meta={
                    "case_id": report.case_id,
                    "rubric": (report.quality_rubric_id or "—"),
                    "judge_tokens": report.judge_tokens,
                },
                title=f"用例 {report.case_id} · Quality Judge",
            )
            (out_dir / f"{report.case_id}-quality.html").write_text(
                quality_trace,
                encoding="utf-8",
            )
    index_path = out_dir / "index.html"
    index_path.write_text(_render_report_index(reports), encoding="utf-8")
    return index_path


def main() -> int:
    """CLI 入口（``python -m grandquiz.evals``）：跑全部用例、打印报告、返回退出码（全绿=0）。"""
    reports = asyncio.run(run_all())
    print(render_report(reports))
    return 0 if all(r.passed for r in reports) else 1
