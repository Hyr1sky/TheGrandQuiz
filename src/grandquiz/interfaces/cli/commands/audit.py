"""``grandquiz audit-doc``——只读核验 DS-S3/4 dogfood 的 trace/DB 证据。"""

import sqlite3
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from grandquiz.evals.document_dogfood import audit_document_dogfood
from grandquiz.interfaces.cli.composition import _resolve_trace_db


def run_document_dogfood_audit_cli(
    *,
    db_path: Path,
    trace_db_path: Path | None,
    ingest_trace_id: str,
    search_trace_id: str,
    max_read_fraction: float,
) -> None:
    console = Console()
    trace_db = _resolve_trace_db(db_path, trace_db_path)
    try:
        report = audit_document_dogfood(
            learning_db=db_path,
            trace_db=trace_db,
            ingest_trace_id=ingest_trace_id,
            search_trace_id=search_trace_id,
            max_read_fraction=max_read_fraction,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise SystemExit(1) from exc
    console.print_json(report.model_dump_json())
    if not report.passed:
        raise SystemExit(1)
