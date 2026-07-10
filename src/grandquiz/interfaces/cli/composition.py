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
from grandquiz.domain.learning.context import learner_context_provider
from grandquiz.domain.learning.fetch import FetchError
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.preference import SqlitePreferenceMemory
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.tools import register_learning_tools
from grandquiz.kernel.clock import SystemClock
from grandquiz.kernel.context import ContextBuilder, Partition
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
    "_LOCAL_HOST",
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
# 本地材料的占位 URL host（fetch 域名白名单放行它；真机远程抓取才走真实域名 + 注入防护）。
_LOCAL_HOST = "local"
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_ROUNDS = 5


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


def build_learning_stores(
    db_path: Path,
) -> tuple[SqliteLearningStore, SqliteLearningMemory, SqlitePreferenceMemory]:
    """建考核会话的三件持久件（store / memory / preference），全落**同一** learning db 文件。

    quiz / react 会话都要这三件同源（薄弱点、偏好与知识共库、跨会话留存）；工厂按固定顺序构造并
    返回，调用方在 finally 里逐个 ``close()``。逐字复刻原编排三行 ``Sqlite*Memory(db_path)``。
    """
    store = SqliteLearningStore(db_path)
    memory = SqliteLearningMemory(db_path)
    preferences = SqlitePreferenceMemory(db_path)  # 偏好与 store / memory 共用同一 learning db
    return store, memory, preferences


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
    materials_dir: Path,
    responder: Responder,
    seed: int,
    max_iterations: int,
) -> Runner:
    """装配真机 ReAct 的 ``Runner``：工具注册 + 版本化系统提示 + ContextBuilder 分区 + Runner 接线。

    逐字复刻 ``run_react`` 内的装配：``ToolRegistry`` 经 ``register_learning_tools`` 注入真依赖
    （SQLite store/memory/preferences + 文件式 fetch 源 + keep-all 审批门 + 注入的 ``responder``
    + ``quiz_seed=seed``）；``load_prompt`` 读版本化 react 系统提示；``ContextBuilder`` 装 system
    前言区 + 学情注入分区（``learner_context_provider`` 闭包，每回合 build 现取最新薄弱点 + 偏好）；
    ``Runner`` 以 ``prompt.version`` 记 prompt 版本进 trace、绑上述 tools / context_builder。参数、
    顺序、默认逐字与原编排一致。
    """
    registry = ToolRegistry()
    register_learning_tools(
        registry,
        source=_file_source(materials_dir),
        provider=provider,
        store=store,
        approval=ScriptedApprovalGate(keep=lambda _item: True),  # MVP keep-all（同 run_ingest）
        memory=memory,
        max_bytes=_DEFAULT_MAX_BYTES,
        allowed_domains={_LOCAL_HOST},
        responder=responder,  # start_quiz 逐题作答（真机 InteractiveResponder）
        preferences=preferences,  # 出题语言偏好透传给 assess_once
        quiz_seed=seed,
    )
    prompt = load_prompt(_REACT_PROMPT_NAME)
    # ContextBuilder（M5）分区装配：system 前言区（版本化 react 系统提示）+ 学情注入分区
    # （domain provider，闭包捕获 store/memory/preferences → 每回合 build 现取最新薄弱点 + 偏好）。
    # domain→kernel 合法：ContextBuilder 只认名字 + 字符串 provider。学情分区内容为空（无薄弱、
    # 无偏好）时 build 自动跳过、不注入空块。预算 / 压缩接缝已在 ContextBuilder 留好（下一程接
    # context compression），本处不设 budget。
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
    return Runner(
        provider=provider,
        emitter=emitter,
        prompt_version=prompt.version,  # prompt 版本号进 trace（架构约束）
        tools=registry,
        max_iterations=max_iterations,
        context_builder=context_builder,
    )
