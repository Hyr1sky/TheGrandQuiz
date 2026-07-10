"""``grandquiz trace`` / ``grandquiz report``——按 trace_id / eval harness 导出自包含 HTML。

复用 issue 03 的 ``kernel.report.render_trace_html``——两命令共用同一渲染器，绝不重实现渲染。
report 逻辑小、并入本模块（同为"导出自包含 HTML"的一族）。
"""

import asyncio
from collections.abc import Iterable
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from grandquiz.interfaces.cli.composition import (
    _DEFAULT_DB,
    _ensure_parent,
    _resolve_trace_db,
)
from grandquiz.kernel.report import render_trace_html
from grandquiz.kernel.trace import Span, TraceStore, build_span_tree

__all__ = ["_run_report_cli", "_run_trace_cli", "export_trace_html"]


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


def _run_report_cli(*, out: Path | None) -> None:
    # 报告不碰 provider / learning 库：全确定性假件驱动 harness，纯导出 HTML。
    from grandquiz.evals.harness import export_html_report

    console = Console()
    out_dir = out if out is not None else _DEFAULT_DB.parent / "eval-report"
    index_path = asyncio.run(export_html_report(out_dir))
    console.print(f"[bold green]eval 报告已导出：[/]{escape(str(index_path))}（浏览器打开）")


def _run_trace_cli(
    *, trace_id: str, db_path: Path, trace_db_path: Path | None, out_path: Path | None
) -> None:
    console = Console()
    trace_db = _resolve_trace_db(db_path, trace_db_path)
    try:
        export_trace_html(trace_id, trace_db_path=trace_db, out_path=out_path, console=console)
    except (ValueError, OSError) as exc:  # 读不到 id / 库路径问题 → 大声报错 + 非零退出
        console.print(f"[red]{escape(str(exc))}[/]")
        raise SystemExit(1) from exc
