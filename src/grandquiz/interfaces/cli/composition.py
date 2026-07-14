"""可复用 composition root——CLI 与未来 Web/API 共用的对象图装配。

**为什么单独一层**：CLI 只是最终交互方式之一（之后还有 Web/API）。散在各命令编排里的对象图
装配（store / memory / preference / TraceStore / EventEmitter / ToolRegistry / Runner /
ContextBuilder 的实例化 + 接线）若留在 CLI 函数里，Web/API 只能复制粘贴同一套接线。故把它剥成
**纯装配工厂 + 共享 helper / 常量**：不含 argparse、不含 console 打印，任何通道（CLI handler /
FastAPI handler）都能调同一套工厂拿到**逐字等价**的对象图（同参数、同顺序、同默认），I/O（参数
解析、打印、tty 交互）留在各通道自己那层。

工厂只做装配、不做生命周期管理：打开的 store / TraceStore 由调用方在 finally 里关闭（沿用各命令
编排既有的 try/finally 收尾），本层不隐藏 close 语义。
"""

from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlparse

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.asked_questions import SqliteAskedQuestionsLedger
from grandquiz.domain.learning.context import learner_context_provider
from grandquiz.domain.learning.ingest.fetch import ALLOW_ANY_DOMAIN, FetchError
from grandquiz.domain.learning.ingest.web_fetch import create_http_source
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.preference import SqlitePreferenceMemory
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.summarizer import LLMSummarizer
from grandquiz.domain.learning.tools import register_learning_tools
from grandquiz.kernel.clock import SystemClock
from grandquiz.kernel.context import (
    BudgetCompressionPolicy,
    ContextBuilder,
    HeuristicTokenCounter,
    Partition,
    SummarizingHistoryCompressor,
)
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import ToolRegistry
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider

# 供 CLI 命令模块 / 未来 Web 通道复用的装配面（列入 __all__ = 视为包内公开，尽管带下划线前缀）。
__all__ = [
    "_DEFAULT_DB",
    "_DEFAULT_MAX_BYTES",
    "_DEFAULT_ROUNDS",
    "_HISTORY_MAX_TURNS",
    "_LOCAL_HOST",
    "_MEMORY_PARTITION_BUDGET",
    "_SYSTEM_PARTITION_BUDGET",
    "_TOTAL_BUDGET",
    "_ensure_parent",
    "_file_source",
    "_resolve_trace_db",
    "build_event_backbone",
    "build_learning_stores",
    "build_react_runner",
]

# ReAct 系统提示的版本化模板名（load_prompt 读 prompts/react_system.md，版本号进 trace）。
_REACT_PROMPT_NAME = "react_system"

# --db 默认库路径：跨会话薄弱点留存的持久 SQLite。
_DEFAULT_DB = Path.home() / ".grandquiz" / "learning.db"
# 独立 trace 库文件名：与 learning.db 分开、同目录（各自 user_version / 迁移序列，互不串号）。
_TRACE_DB_NAME = "trace.db"
# 本地材料的占位 URL host——`grandquiz ingest` 子命令（commands/ingest.py，只吃本地文件）仍用它
# 当唯一放行的域名；`grandquiz react`（build_react_runner）已放开为 ALLOW_ANY_DOMAIN + 真实网络
# 抓取（web_fetch 的 SSRF 检查是那条路径真正的安全边界，不是域名预批）。
_LOCAL_HOST = "local"
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_ROUNDS = 5

# Context compression（C-wire 增量 1，见 .scratch/context-compression/PRD.md + gap-review）：
# system 分区实测 ~925 token（react_system.md），memory 分区随薄弱点/资源目录增长；两个 budget
# 都留数倍实测值的余量（防未来提示/目录膨胀被静默头截断，同时远低于 deepseek-chat 真实上下文窗口）。
# total_budget 刻意设得比 system+memory+history 之和更保守：_enforce_total_budget 只在 build()
# 时查一次（run_agent_turn 的 tool-calling 循环内追加的消息不再复查，见 gap-review 已知缺口），
# 故硬上限须留够 tool 往返 + tool_specs 的隐性余量，不能设成贴近真实窗口的数字。
_SYSTEM_PARTITION_BUDGET = 4_000
_MEMORY_PARTITION_BUDGET = 6_000
_TOTAL_BUDGET = 20_000
# 历史滑动窗口：保最近 5 轮原样（先滑窗，PRD 排序里的老轮摘要留下一程真 Summarizer 接入时换）。
_HISTORY_MAX_TURNS = 5


def _ensure_parent(db_path: Path) -> None:
    """自动建 db 文件的父目录（``~/.grandquiz`` 首次运行时不存在）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _resolve_trace_db(db_path: Path, trace_db_path: Path | None) -> Path:
    """独立 trace 库路径：显式传入优先，否则默认与 learning.db 同目录的 ``trace.db``。

    两库各自 ``user_version``——同路径会让 learning 迁移先占版本号、TraceStore 跳过建 ``events`` 表、
    ``record`` 被隔离静默吞掉 → trace 静默为空（违背"大声失败"），故拒绝同路径。
    """
    resolved = trace_db_path if trace_db_path is not None else db_path.parent / _TRACE_DB_NAME
    if resolved == db_path:
        raise ValueError(f"trace 库不能与 learning 库同路径（{db_path}）——请分开")
    return resolved


def _file_source(materials_dir: Path) -> Callable[[str], str]:
    """建**文件式** fetch 源（复用现有 ingest 那套读本地材料，非 httpx——真远程抓取仍缓办）。

    把 ``file://local/<相对路径>`` 的 url 映射到 ``materials_dir/<相对路径>`` 读取；文件不存在等 IO
    异常由 ``fetch_resource`` 归一成 ``FetchError`` → ingest 走优雅失败分支（不炸整条会话）。url 的
    ``local`` host 必在 ingest 的域名白名单里（见 ``run_react`` 的 ``allowed_domains``）。

    **路径穿越守卫**：url 的 path 是不可信输入——``file://local/../../etc/passwd`` 之类含
    ``..`` / 绝对路径的 payload 会逃出 ``materials_dir`` 读任意文件。故解析后 ``resolve()`` 归一，
    再校验最终路径仍在 ``materials_dir`` 内（``is_relative_to``）；越界一律**拒**（抛
    ``FetchError``、报清楚哪个文件不在材料目录内）——``fetch_resource`` 原样透传 ``FetchError``，
    ingest 走优雅失败分支、不炸会话。正常的 ``file://local/<名>`` / 目录内嵌套相对路径不受影响。
    """
    base = materials_dir.resolve()

    def source(url: str) -> str:
        relative = urlparse(url).path.lstrip("/")
        target = (base / relative).resolve()
        if not target.is_relative_to(base):
            raise FetchError(f"文件 {relative} 不在材料目录 {base} 内")
        return target.read_text(encoding="utf-8")

    return source


def _web_and_file_source(materials_dir: Path) -> Callable[[str], str]:
    """派发式抓取源：``file://local/<相对路径>`` 走本地材料读取；``http(s)://`` 走真实网络
    抓取（``web_fetch.create_http_source``，含 SSRF 防护 + 逐跳重定向重验证）。两条路径合成
    一个 ``source`` 可调用体统一注入 ``fetch_resource``——它不关心 url 是本地文件还是真实
    网页，只认这一个 callable；域名白名单相应放开为 ``ALLOW_ANY_DOMAIN``（见 ``fetch.py`` 的
    职责划分：白名单管"允不允许抓"，``web_fetch`` 的 SSRF 检查管"抓的时候会不会被骗去打内网"）。
    """
    file_source = _file_source(materials_dir)
    http_source = create_http_source()

    def source(url: str) -> str:
        if urlparse(url).scheme in ("http", "https"):
            return http_source(url)
        return file_source(url)

    return source


def build_learning_stores(
    db_path: Path,
) -> tuple[
    SqliteLearningStore, SqliteLearningMemory, SqlitePreferenceMemory, SqliteAskedQuestionsLedger
]:
    """建考核会话的四件持久件（store / memory / preference / asked_questions），全落**同一**
    learning db 文件。

    quiz / react 会话都要这四件同源（薄弱点、偏好、已问过台账与知识共库、跨会话留存）；工厂按
    固定顺序构造并返回，调用方在 finally 里逐个 ``close()``。``asked_questions``（skeleton-ledger.md
    #8）是第四件——修"关掉 CLI 重开、复考同一薄弱概念被逐字重问旧题"这个真实 bug。
    """
    store = SqliteLearningStore(db_path)
    memory = SqliteLearningMemory(db_path)
    preferences = SqlitePreferenceMemory(db_path)  # 偏好与 store / memory 共用同一 learning db
    asked_questions = SqliteAskedQuestionsLedger(db_path)
    return store, memory, preferences, asked_questions


def build_event_backbone(
    trace_db_path: Path,
    *,
    trace_id: str,
    subscribers: Iterable[object] = (),
) -> tuple[EventEmitter, TraceStore]:
    """建事件脊柱三件套：``EventSink`` + 注册的 ``TraceStore`` processor + ``EventEmitter``。

    ``subscribers`` 为需订阅的观察者（如 ``QuizEventPrinter``），在 ``register(TraceStore)``
    **之前**逐个 ``subscribe``——逐字复刻各命令原来的 subscribe→register→EventEmitter 顺序（ingest
    无观察者即空序列，只 register + emitter）。观察者是纯数据对象、由调用方先构造好传入，其构造
    相对 ``EventSink`` 的先后无可观察副作用。返回 ``(emitter, trace_store)``，``trace_store`` 由调用
    方在 finally 关闭。
    """
    sink = EventSink()
    for subscriber in subscribers:
        sink.subscribe(subscriber)  # type: ignore[arg-type]
    trace_store = TraceStore(trace_db_path)
    sink.register(trace_store)  # 消费者即 processor：真机事件流落独立 trace 库
    emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)
    return emitter, trace_store


def build_react_runner(
    *,
    provider: Provider,
    emitter: EventEmitter,
    store: SqliteLearningStore,
    memory: SqliteLearningMemory,
    preferences: SqlitePreferenceMemory,
    asked_questions: SqliteAskedQuestionsLedger,
    materials_dir: Path,
    responder: Responder,
    seed: int,
    max_iterations: int,
) -> Runner:
    """装配真机 ReAct 的 ``Runner``：工具注册 + 版本化系统提示 + ContextBuilder 分区 + Runner 接线。

    逐字复刻 ``run_react`` 内的装配：``ToolRegistry`` 经 ``register_learning_tools`` 注入真依赖
    （SQLite store/memory/preferences + 派发式 fetch 源——``file://local/<名>`` 走本地材料、
    ``http(s)://`` 走真实网络抓取（``web_fetch.create_http_source``，含 SSRF 防护）+ keep-all
    审批门 + 注入的 ``responder`` + ``quiz_seed=seed``）；域名白名单相应放开为
    ``ALLOW_ANY_DOMAIN``（个人工具"粘贴任意文章 URL"场景下预先登记域名不现实，真正的安全边界
    在 ``web_fetch`` 的 SSRF 检查，不在域名预批）；``load_prompt`` 读版本化 react 系统提示；
    ``ContextBuilder`` 装 system 前言区 + 学情注入分区（``learner_context_provider`` 闭包，每
    回合 build 现取最新薄弱点 + 偏好）；``Runner`` 以 ``prompt.version`` 记 prompt 版本进
    trace、绑上述 tools / context_builder。

    Context compression：分区各带 ``budget``，经 ``BudgetCompressionPolicy`` 头截断（C-wire 增量
    1）；``counter`` + ``total_budget`` 给总硬上限（超限抛 ``ContextBudgetExceeded``，大声失败）；
    ``history_compressor`` 用 ``SummarizingHistoryCompressor``（保最近 ``_HISTORY_MAX_TURNS`` 轮
    原样，被挤出的老轮经 ``LLMSummarizer``（真 LLM，role=basic）折进滚动摘要，C-wire 增量 3——
    ``Runner`` 每轮成功后台排折叠任务、下一轮开头收口、失败隔离，见 ``kernel/runner.py``）。现有
    短会话（远低于 ``_HISTORY_MAX_TURNS`` 轮）不触发折叠，``build()`` 逐字节等价此前（cassette /
    既有测试不受影响）。
    """
    registry = ToolRegistry()
    register_learning_tools(
        registry,
        source=_web_and_file_source(materials_dir),
        provider=provider,
        store=store,
        approval=ScriptedApprovalGate(keep=lambda _item: True),  # MVP keep-all（同 run_ingest）
        memory=memory,
        max_bytes=_DEFAULT_MAX_BYTES,
        allowed_domains=ALLOW_ANY_DOMAIN,
        responder=responder,  # start_quiz 逐题作答（真机 InteractiveResponder）
        preferences=preferences,  # 出题语言偏好透传给 assess_once
        quiz_seed=seed,
        asked_questions=asked_questions,  # 跨会话去重台账（skeleton-ledger.md #8）
    )
    prompt = load_prompt(_REACT_PROMPT_NAME)
    # ContextBuilder（M5）分区装配：system 前言区（版本化 react 系统提示）+ 学情注入分区
    # （domain provider，闭包捕获 store/memory/preferences → 每回合 build 现取最新薄弱点 + 偏好）。
    # domain→kernel 合法：ContextBuilder 只认名字 + 字符串 provider。学情分区内容为空（无薄弱、
    # 无偏好）时 build 自动跳过、不注入空块。预算见下方 budget 常量（C-wire 增量 1）；
    # history_compressor 见下方 SummarizingHistoryCompressor（真 LLMSummarizer，C-wire 增量 3）。
    counter = HeuristicTokenCounter()
    context_builder = ContextBuilder(
        [
            Partition(name="system", provider=prompt.text, budget=_SYSTEM_PARTITION_BUDGET),
            Partition(
                name="memory",
                provider=learner_context_provider(
                    store=store, memory=memory, preferences=preferences
                ),
                budget=_MEMORY_PARTITION_BUDGET,
            ),
        ],
        policy=BudgetCompressionPolicy(counter),
        counter=counter,
        total_budget=_TOTAL_BUDGET,
        history_compressor=SummarizingHistoryCompressor(
            LLMSummarizer(provider, emitter), max_turns=_HISTORY_MAX_TURNS
        ),
    )
    return Runner(
        provider=provider,
        emitter=emitter,
        prompt_version=prompt.version,  # prompt 版本号进 trace（架构约束）
        tools=registry,
        max_iterations=max_iterations,
        context_builder=context_builder,
    )
