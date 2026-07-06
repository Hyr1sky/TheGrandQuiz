"""grandquiz CLI——argparse 子命令路由：``ingest``（喂材料入库）/ ``quiz``（逐题交互考核）。

CLI 是事件脊柱的消费者：``quiz`` 把 ``QuizEventPrinter`` 订阅到考核事件流做 Rich 呈现，不另起
渲染逻辑（呼应架构卖点）。两个子命令都用真 ``OpenAICompatProvider.from_env()`` + 持久化 SQLite
（``--db`` 默认 ``~/.grandquiz/learning.db``，自动建父目录；store / memory 同一 db 文件，薄弱点
跨会话留存）。真机交互试跑（``grandquiz quiz`` 的 tty 逐题）留给 human；``run_ingest`` /
``run_quiz`` 把 provider / responder / console 作参数注入，故可测的粘合（文件读取 / 存在性检查 /
空库分支 / 薄弱小结 / 事件呈现）都能用假件驱动断言，不碰真实 tty 或 LLM。

无子命令 → 打印帮助（旧 ``repl.py`` 入口仍可用，见 ``repl.main``）。
"""

import argparse
import asyncio
import contextlib
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.ingest import IngestResult, ingest_resource
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.models import LearningTask
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.interfaces.cli.interactive import InteractiveResponder
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.kernel.clock import SystemClock, new_rng
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.providers.base import Provider
from grandquiz.providers.llm import OpenAICompatProvider

# --db 默认库路径：跨会话薄弱点留存的持久 SQLite。
_DEFAULT_DB = Path.home() / ".grandquiz" / "learning.db"
# 本地材料的占位 URL host（fetch 域名白名单放行它；真机远程抓取才走真实域名 + 注入防护）。
_LOCAL_HOST = "local"
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_ROUNDS = 5


def _ensure_parent(db_path: Path) -> None:
    """自动建 db 文件的父目录（``~/.grandquiz`` 首次运行时不存在）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)


async def run_ingest(
    *,
    title: str,
    material_path: Path,
    db_path: Path,
    provider: Provider,
    console: Console,
) -> IngestResult:
    """读本地材料 → 真 Reader 深读 → 审批（MVP keep-all）→ 入 SQLite。返回 ``IngestResult``。

    ``source`` 注入文件内容、``url`` 用本地占位（域名白名单只放行 ``_LOCAL_HOST``）；``provider`` /
    ``console`` 作参数注入以便测试用假件驱动。``max_bytes`` 取内容实际字节与默认上限的较大者
    （本地材料不受远程大小上限约束，但仍走同一守卫路径）。
    """
    content = material_path.read_text(encoding="utf-8")
    _ensure_parent(db_path)
    store = SqliteLearningStore(db_path)
    task = LearningTask.create(title)
    url = f"file://{_LOCAL_HOST}/{material_path.name}"

    emitter = EventEmitter(EventSink(), SystemClock(), trace_id=uuid.uuid4().hex)
    try:
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
    _print_ingest_result(console, title, result)
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
) -> None:
    """对 ``title`` 任务跑 ``rounds`` 轮逐题考核；空库 → 提示先 ingest。会话结束打印薄弱点小结。

    ``QuizEventPrinter`` 订阅事件流做 Rich 呈现（CLI = 事件脊柱的投影）。``responder`` 取消作答
    （``InteractiveResponder`` 抛 ``KeyboardInterrupt``）→ 优雅退出本次会话、仍打印已积累的薄弱点。
    ``rng`` 用可变种子（CLI 非 replay）：每轮 ``new_rng(seed + 轮次)``。
    """
    _ensure_parent(db_path)
    store = SqliteLearningStore(db_path)
    memory = SqliteLearningMemory(db_path)
    try:
        task = LearningTask.create(title)
        if not store.items_for_task(task.task_id):
            _print_needs_ingest(console, title)
            return

        sink = EventSink()
        sink.subscribe(QuizEventPrinter(console))
        console.print(f"[bold]开始考核「{title}」——共 {rounds} 轮（Ctrl+C 随时退出）[/]")
        try:
            for round_index in range(rounds):
                console.rule(f"第 {round_index + 1} / {rounds} 轮")
                emitter = EventEmitter(sink, SystemClock(), trace_id=uuid.uuid4().hex)
                await assess_once(
                    task,
                    store=store,
                    provider=provider,
                    responder=responder,
                    memory=memory,
                    emitter=emitter,
                    rng=new_rng(seed + round_index),
                )
        except KeyboardInterrupt:
            console.print("\n[dim]已退出本次考核会话。[/]")

        _print_weak_summary(console, store, memory, task)
    finally:
        store.close()
        memory.close()


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


async def _run_quiz_cli(*, title: str, rounds: int, db_path: Path) -> None:
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
            asyncio.run(_run_quiz_cli(title=args.title, rounds=args.rounds, db_path=args.db))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
