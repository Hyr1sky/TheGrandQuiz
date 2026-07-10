"""``grandquiz`` CLI 各子命令的编排 + handler + console 打印，按命令分模块。

每个模块（ingest / quiz / react / trace）放该命令的 orchestration（``run_*`` / ``export_*``）+ CLI
handler（``_run_*_cli``）+ 该命令自己的 console 打印 helper；对象图装配一律调
``grandquiz.interfaces.cli.composition`` 的工厂（不在此重复接线）。此处 ``__init__`` 只留一个
**跨命令共用**的 console helper（``_print_trace_location``——ingest/quiz/react 会话结束都打印它）。
"""

from pathlib import Path

from rich.console import Console
from rich.markup import escape

__all__ = ["_print_trace_location"]


def _print_trace_location(console: Console, trace_id: str, trace_db_path: Path) -> None:
    """会话结束打印 ``trace_id`` + 独立 trace 库位置（便于随手 ``grandquiz trace <id>`` 复盘）。"""
    console.print(
        f"[dim]本次会话 trace：[bold]{trace_id}[/]（存于 {escape(str(trace_db_path))}）[/]"
    )
