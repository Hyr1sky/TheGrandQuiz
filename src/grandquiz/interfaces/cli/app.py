"""grandquiz CLI——argparse 子命令路由：``ingest``（喂材料入库）/ ``quiz``（逐题交互考核）。

CLI 是事件脊柱的消费者：``quiz`` 把 ``QuizEventPrinter`` 订阅到考核事件流做 Rich 呈现，不另起
渲染逻辑（呼应架构卖点）。两个子命令都用真 ``OpenAICompatProvider.from_env()`` + 持久化 SQLite
（``--db`` 默认 ``~/.grandquiz/learning.db``，自动建父目录；store / memory 同一 db 文件，薄弱点
跨会话留存）。真机交互试跑（``grandquiz quiz`` 的 tty 逐题）留给 human；``run_ingest`` /
``run_quiz`` 把 provider / responder / console 作参数注入，故可测的粘合（文件读取 / 存在性检查 /
空库分支 / 薄弱小结 / 事件呈现）都能用假件驱动断言，不碰真实 tty 或 LLM。

无子命令 → 打印帮助。
"""

import argparse
import asyncio
import contextlib
import time
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.grading import GradingError
from grandquiz.domain.learning.ingest import IngestResult, ingest_resource
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.models import LearningTask
from grandquiz.domain.learning.preference import QUESTION_LANGUAGE_KEY, SqlitePreferenceMemory
from grandquiz.domain.learning.question import QuestionError
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.interfaces.cli.interactive import InteractiveResponder
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.kernel.clock import SystemClock, new_rng
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.report import render_trace_html
from grandquiz.kernel.trace import Span, TraceStore, build_span_tree
from grandquiz.providers.base import Provider
from grandquiz.providers.llm import OpenAICompatProvider

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


def _print_trace_location(console: Console, trace_id: str, trace_db_path: Path) -> None:
    """会话结束打印 ``trace_id`` + 独立 trace 库位置（便于随手 ``grandquiz trace <id>`` 复盘）。"""
    console.print(
        f"[dim]本次会话 trace：[bold]{trace_id}[/]（存于 {escape(str(trace_db_path))}）[/]"
    )


async def run_ingest(
    *,
    title: str,
    material_path: Path,
    db_path: Path,
    provider: Provider,
    console: Console,
    trace_db_path: Path | None = None,
) -> IngestResult:
    """读本地材料 → 真 Reader 深读 → 审批（MVP keep-all）→ 入 SQLite。返回 ``IngestResult``。

    ``source`` 注入文件内容、``url`` 用本地占位（域名白名单只放行 ``_LOCAL_HOST``）；``provider`` /
    ``console`` 作参数注入以便测试用假件驱动。``max_bytes`` 取内容实际字节与默认上限的较大者
    （本地材料不受远程大小上限约束，但仍走同一守卫路径）。

    本次会话生成一个 ``trace_id``，并把发射的 AgentEvent 流经 ``EventSink.register`` 注册的
    ``TraceStore`` 落进**独立 trace 库**（默认与 learning.db 同目录的 ``trace.db``）——落 trace 纯
    经"注册 processor"实现，``ingest_resource`` 签名逻辑一行不改（可观测是脊柱投影、非业务耦合）。
    会话结束打印 ``trace_id`` + 库位置。
    """
    content = material_path.read_text(encoding="utf-8")
    _ensure_parent(db_path)
    resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
    _ensure_parent(resolved_trace_db)
    store = SqliteLearningStore(db_path)
    trace_store: TraceStore | None = None  # try 内构造 + None-guard 关闭，建失败不泄漏 store
    task = LearningTask.create(title)
    url = f"file://{_LOCAL_HOST}/{material_path.name}"
    trace_id = uuid.uuid4().hex
    try:
        sink = EventSink()
        trace_store = TraceStore(resolved_trace_db)
        sink.register(trace_store)  # 消费者即 processor：真机事件流落独立 trace 库
        emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)
        result = await ingest_resource(
            task,
            url,
            source=lambda _url: content,
            provider=provider,
            store=store,
            approval=ScriptedApprovalGate(keep=lambda _item: True),
            emitter=emitter,
            max_bytes=max(_DEFAULT_MAX_BYTES, len(content.encode("utf-8")) + 1),
            allowed_domains={_LOCAL_HOST},
        )
    finally:
        store.close()
        if trace_store is not None:
            trace_store.close()
    _print_ingest_result(console, title, result)
    _print_trace_location(console, trace_id, resolved_trace_db)
    return result


def _print_ingest_result(console: Console, title: str, result: IngestResult) -> None:
    if result.status == "failed":
        console.print(f"[red]深读失败：材料未能入库（任务「{title}」）。[/]")
        return
    if not result.items:
        console.print(f"[yellow]深读完成但没有抽出知识点（任务「{title}」）。[/]")
        return
    console.print(f"[bold green]已入库 {len(result.items)} 个知识点（任务「{title}」）：[/]")
    for item in result.items:
        console.print(f"  · [bold]{item.concept}[/] — {item.summary}")


async def run_quiz(
    *,
    title: str,
    rounds: int,
    db_path: Path,
    provider: Provider,
    responder: Responder,
    console: Console,
    seed: int,
    trace_db_path: Path | None = None,
    prefer_lang: str | None = None,
) -> None:
    """对 ``title`` 任务跑 ``rounds`` 轮逐题考核；空库 → 提示先 ingest。会话结束打印薄弱点小结。

    ``QuizEventPrinter`` 订阅事件流做 Rich 呈现（CLI = 事件脊柱的投影）。``responder`` 取消作答
    （``InteractiveResponder`` 抛 ``KeyboardInterrupt``）→ 优雅退出本次会话、仍打印已积累的薄弱点。
    某轮出题 / 判卷重试用尽（``QuestionError`` / ``GradingError``）→ 只跳过该轮、继续下一轮，不崩
    整场会话（M6 RecoveryPolicy 的临时兜底，见 docs/skeleton-ledger.md #7）。
    ``rng`` 用可变种子（CLI 非 replay）：每轮 ``new_rng(seed + 轮次)``。

    ``prefer_lang``：非 None 时先显式把 ``question_language`` 偏好写进持久 SQLite（跨会话留存），
    再下传 Preference Memory 给 ``assess_once``——出题语言按 **偏好 > task 默认 > 中文** 覆盖。
    偏好台账**每次会话都构造并下传**（哪怕本次未设），故上次会话设过的语言偏好本次仍生效。

    **每次会话一个 ``trace_id``**（一个 ``EventEmitter`` 贯穿全部轮次，故 ``seq`` / span id 跨轮
    唯一、落库后是一条 trace、每轮一棵 assessment 根 span）；发射的 AgentEvent 流经
    ``EventSink.register`` 注册的 ``TraceStore`` 落进**独立 trace 库**（默认与 learning.db 同目录的
    ``trace.db``）——落 trace 纯经"注册 processor"实现，``assess_once`` 签名逻辑一行不改（可观测
    是脊柱投影、非业务耦合）。会话结束打印 ``trace_id`` + 库位置。
    """
    _ensure_parent(db_path)
    store = SqliteLearningStore(db_path)
    memory = SqliteLearningMemory(db_path)
    preferences = SqlitePreferenceMemory(db_path)  # 偏好与 store / memory 共用同一 learning db
    if prefer_lang is not None:
        # 显式设置出题语言偏好（confidence 恒 1.0），跨会话留存、后续覆盖 task 默认语言。
        preferences.set_preference(QUESTION_LANGUAGE_KEY, prefer_lang)
    trace_store: TraceStore | None = None  # 空库分支不落 trace（无会话）；在 finally 里择机关闭
    try:
        task = LearningTask.create(title)
        if not store.items_for_task(task.task_id):
            _print_needs_ingest(console, title)
            return

        resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
        _ensure_parent(resolved_trace_db)
        trace_id = uuid.uuid4().hex
        sink = EventSink()
        sink.subscribe(QuizEventPrinter(console))
        trace_store = TraceStore(resolved_trace_db)
        sink.register(trace_store)  # 消费者即 processor：真机事件流落独立 trace 库
        # 一个 emitter 贯穿全会话：跨轮共享 trace_id，seq / span id 单调唯一（不逐轮重置、不撞号）。
        emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)
        # SKELETON: 会话内进程内"已问过"台账（item_id → 已问过的题目文本），跨轮累积、经 assess_once
        # 下传出题函数做去重——复考同一薄弱概念时每轮换角度、不逐字重问。正式版是与 Learning Memory
        # 并列的跨会话 SQLite 去重表（跨会话持久），见 docs/skeleton-ledger.md #8。
        recently_asked: dict[str, list[str]] = {}
        console.print(f"[bold]开始考核「{title}」——共 {rounds} 轮（Ctrl+C 随时退出）[/]")
        try:
            for round_index in range(rounds):
                console.rule(f"第 {round_index + 1} / {rounds} 轮")
                try:
                    await assess_once(
                        task,
                        store=store,
                        provider=provider,
                        responder=responder,
                        memory=memory,
                        emitter=emitter,
                        rng=new_rng(seed + round_index),
                        recently_asked=recently_asked,
                        preferences=preferences,
                    )
                except (QuestionError, GradingError) as exc:
                    # SKELETON(M6): 优雅降级本属 kernel RecoveryPolicy 统一裁决；MVP 先在 CLI 边界
                    # 兜底——出题 / 判卷重试用尽（LLM 未能产合法输出）只跳过本轮、不崩整场会话。
                    # 刻意只兜这两类"本轮可恢复"失败：assess_once 仍原样冒泡所有异常（保 eval /
                    # replay 契约——ReplayMiss / provider 传输错误等会话级失败不该被静默吞），
                    # 由 CLI 这个生产界面自行选择降级策略。见 docs/skeleton-ledger.md。
                    console.print(f"[yellow]本轮跳过（{escape(str(exc))}）[/]")
                    continue
        except KeyboardInterrupt:
            console.print("\n[dim]已退出本次考核会话。[/]")

        _print_weak_summary(console, store, memory, task)
        _print_trace_location(console, trace_id, resolved_trace_db)
    finally:
        store.close()
        memory.close()
        preferences.close()
        if trace_store is not None:
            trace_store.close()


def _print_needs_ingest(console: Console, title: str) -> None:
    console.print(
        f"[yellow]任务「{title}」还没有知识库。先运行 "
        f"[bold]grandquiz ingest <材料文件> --task {title}[/] 喂材料再来考核。[/]"
    )


def _print_weak_summary(
    console: Console,
    store: SqliteLearningStore,
    memory: SqliteLearningMemory,
    task: LearningTask,
) -> None:
    weak_ids = memory.weak_item_ids()
    if not weak_ids:
        console.print("[green]本次考核后没有遗留薄弱点，全部掌握。[/]")
        return
    concept_by_id = {item.item_id: item.concept for item in store.items_for_task(task.task_id)}
    console.print("[bold]薄弱点小结（已跨会话留存，下次优先考）：[/]")
    for item_id in sorted(weak_ids):
        state = memory.state_of(item_id)
        console.print(f"  · {concept_by_id.get(item_id, item_id)} — {state}")


# --- 导出子命令（report / trace → 自包含 HTML）------------------------------------------------
#
# 复用 issue 03 的 kernel.report.render_trace_html——两命令共用同一渲染器，绝不重实现渲染。


def _sum_span_tokens(spans: Iterable[Span]) -> int:
    """递归汇总 span 森林的 token 用量（复用 ``Span.tokens``，其底层是 ``Usage.total_tokens``）。"""
    total = 0
    for span in spans:
        if span.tokens is not None:
            total += span.tokens
        total += _sum_span_tokens(span.children)
    return total


def export_trace_html(
    trace_id: str,
    *,
    trace_db_path: Path,
    out_path: Path | None = None,
    console: Console | None = None,
) -> Path:
    """从独立 trace 库按 ``trace_id`` 读出某次会话 → ``render_trace_html`` → 写自包含 HTML 文件。

    读不到该 ``trace_id`` → **大声报错**（抛 ``ValueError``），绝不静默产出空报告。默认输出路径为
    trace 库同目录的 ``trace-<id>.html``。返回产出文件路径。
    """
    store = TraceStore(trace_db_path)
    try:
        events = store.events(trace_id)
    finally:
        store.close()
    if not events:
        raise ValueError(
            f"trace 未找到：{trace_id}（库 {trace_db_path}）"
            "——确认 trace_id 与 --trace-db 正确（每次会话结束会打印 trace_id + 库位置）"
        )
    spans = build_span_tree(events)
    meta: dict[str, object] = {
        "trace_id": trace_id,
        "event_count": len(events),
        "total_tokens": _sum_span_tokens(spans),
    }
    document = render_trace_html(events, spans, meta=meta, title=f"Trace {trace_id}")
    resolved_out = (
        out_path if out_path is not None else trace_db_path.parent / f"trace-{trace_id}.html"
    )
    _ensure_parent(resolved_out)
    resolved_out.write_text(document, encoding="utf-8")
    if console is not None:
        console.print(f"[bold green]trace HTML 已导出：[/]{escape(str(resolved_out))}")
    return resolved_out


async def _run_ingest_cli(*, title: str, material_path: Path, db_path: Path) -> None:
    provider = OpenAICompatProvider.from_env()
    try:
        await run_ingest(
            title=title,
            material_path=material_path,
            db_path=db_path,
            provider=provider,
            console=Console(),
        )
    finally:
        await provider.aclose()


async def _run_quiz_cli(
    *, title: str, rounds: int, db_path: Path, prefer_lang: str | None = None
) -> None:
    # 先查库（不构造 provider）：空库 / 错任务直接给指引——无需 LLM key，也免去无谓 HTTP 客户端。
    console = Console()
    _ensure_parent(db_path)
    store = SqliteLearningStore(db_path)
    try:
        has_items = bool(store.items_for_task(LearningTask.create(title).task_id))
    finally:
        store.close()
    if not has_items:
        _print_needs_ingest(console, title)
        return

    provider = OpenAICompatProvider.from_env()
    try:
        await run_quiz(
            title=title,
            rounds=rounds,
            db_path=db_path,
            provider=provider,
            responder=InteractiveResponder(),
            console=console,
            seed=int(time.time()),  # CLI 非 replay：可变种子（每次会话不同选题次序）
            prefer_lang=prefer_lang,
        )
    finally:
        await provider.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grandquiz", description="考核驱动的个人学习工具")
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser("ingest", help="读本地材料 → 深读 → 入库")
    p_ingest.add_argument("material_file", type=Path, help="本地材料文件路径")
    p_ingest.add_argument("--task", required=True, help="学习任务标题（考核范围）")
    p_ingest.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")

    p_quiz = sub.add_parser("quiz", help="对某任务逐题交互考核")
    p_quiz.add_argument("title", help="学习任务标题")
    p_quiz.add_argument("--rounds", type=int, default=_DEFAULT_ROUNDS, help="考核轮数")
    p_quiz.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")
    p_quiz.add_argument(
        "--prefer-lang",
        default=None,
        help="显式设出题语言偏好（如 英文 / en），跨会话留存并覆盖任务默认语言",
    )

    p_report = sub.add_parser("report", help="跑 eval harness → 导出自包含 HTML 报告")
    p_report.add_argument(
        "--out", type=Path, default=None, help="报告输出目录（默认 ~/.grandquiz/eval-report）"
    )

    p_trace = sub.add_parser("trace", help="按 trace_id 从 trace 库导出自包含 HTML")
    p_trace.add_argument("trace_id", help="要导出的会话 trace_id（会话结束时打印过）")
    p_trace.add_argument(
        "--db", type=Path, default=_DEFAULT_DB, help="learning 库路径（派生默认 trace 库位置）"
    )
    p_trace.add_argument(
        "--trace-db", type=Path, default=None, help="独立 trace 库路径（默认同目录 trace.db）"
    )
    p_trace.add_argument(
        "--out", type=Path, default=None, help="输出 HTML 文件（默认 trace-<id>.html）"
    )

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """子命令路由入口（``[project.scripts] grandquiz``）。无子命令 → 打印帮助。

    启动即 ``load_dotenv()``（从 cwd 向上找 ``.env``）：让 ``grandquiz`` 在仓库里开箱可用、
    无需每次 ``--env-file``；已在环境里的变量不覆盖（``uv run --env-file .env`` 仍兼容）。
    """
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_ingest_cli(title=args.task, material_path=args.material_file, db_path=args.db)
            )
    elif args.command == "quiz":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_quiz_cli(
                    title=args.title,
                    rounds=args.rounds,
                    db_path=args.db,
                    prefer_lang=args.prefer_lang,
                )
            )
    elif args.command == "report":
        # 报告不碰 provider / learning 库：全确定性假件驱动 harness，纯导出 HTML。
        from grandquiz.evals.harness import export_html_report

        console = Console()
        out_dir = args.out if args.out is not None else _DEFAULT_DB.parent / "eval-report"
        index_path = asyncio.run(export_html_report(out_dir))
        console.print(f"[bold green]eval 报告已导出：[/]{escape(str(index_path))}（浏览器打开）")
    elif args.command == "trace":
        console = Console()
        trace_db = _resolve_trace_db(args.db, args.trace_db)
        try:
            export_trace_html(
                args.trace_id, trace_db_path=trace_db, out_path=args.out, console=console
            )
        except (ValueError, OSError) as exc:  # 读不到 id / 库路径问题 → 大声报错 + 非零退出
            console.print(f"[red]{escape(str(exc))}[/]")
            raise SystemExit(1) from exc
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
