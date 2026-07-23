"""``grandquiz ingest``——读本地材料 → 真 Reader 深读 → 人工审批 → 入 SQLite。"""

import uuid
from pathlib import Path
from urllib.parse import quote

from rich.console import Console

from grandquiz.domain.learning.approval import ApprovalCancelled, ApprovalGate
from grandquiz.domain.learning.ingest import IngestResult, ingest_resource
from grandquiz.domain.learning.models import derive_id
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.interfaces.cli.approval import CliApprovalGate
from grandquiz.interfaces.cli.commands import _print_trace_location
from grandquiz.interfaces.cli.composition import (
    _DEFAULT_MAX_BYTES,
    _LOCAL_HOST,
    _ensure_parent,
    _resolve_trace_db,
    budget_provider,
    build_event_backbone,
)
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider
from grandquiz.providers.llm import OpenAICompatProvider

__all__ = ["_run_ingest_cli", "run_ingest"]


def _local_material_url(material_path: Path) -> str:
    """Build a stable, non-disclosing locator for one local file."""
    path_token = derive_id(str(material_path.resolve()))
    return f"file://{_LOCAL_HOST}/{path_token}/{quote(material_path.name, safe='')}"


async def run_ingest(
    *,
    title: str,
    material_path: Path,
    db_path: Path,
    provider: Provider,
    approval: ApprovalGate,
    console: Console,
    trace_db_path: Path | None = None,
) -> IngestResult:
    """读本地材料 → 真 Reader 深读 → 注入的审批门 → 入 SQLite。返回 ``IngestResult``。

    ``source`` 注入文件内容、``url`` 用本地占位（域名白名单只放行 ``_LOCAL_HOST``）；``provider`` /
    ``console`` 作参数注入以便测试用假件驱动。``max_bytes`` 取内容实际字节与默认上限的较大者
    （本地材料不受远程大小上限约束，但仍走同一守卫路径）。

    本次会话生成一个 ``trace_id``，并把发射的 AgentEvent 流经 ``EventSink.register`` 注册的
    ``TraceStore`` 落进**独立 trace 库**（默认与 learning.db 同目录的 ``trace.db``）——落 trace 纯
    经"注册 processor"实现，``ingest_resource`` 签名逻辑一行不改（可观测是脊柱投影、非业务耦合）。
    会话结束打印 ``trace_id`` + 库位置。
    """
    content = material_path.read_text(encoding="utf-8")
    provider = budget_provider(provider)
    _ensure_parent(db_path)
    resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
    _ensure_parent(resolved_trace_db)
    store = SqliteLearningStore(db_path)
    trace_store: TraceStore | None = None  # try 内构造 + None-guard 关闭，建失败不泄漏 store
    url = _local_material_url(material_path)
    trace_id = uuid.uuid4().hex
    try:
        emitter, trace_store = build_event_backbone(resolved_trace_db, trace_id=trace_id)
        result = await ingest_resource(
            url,
            source=lambda _url: content,
            provider=provider,
            store=store,
            approval=approval,
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
        console.print(f"[red]深读失败：材料未能入库（入库标签「{title}」）。[/]")
        return
    if not result.items:
        console.print(f"[yellow]深读完成但没有抽出知识点（入库标签「{title}」）。[/]")
        return
    console.print(f"[bold green]已入库 {len(result.items)} 个知识点（入库标签「{title}」）：[/]")
    for item in result.items:
        console.print(f"  · [bold]{item.concept}[/] — {item.summary}")


async def _run_ingest_cli(*, title: str, material_path: Path, db_path: Path) -> None:
    console = Console()
    provider = OpenAICompatProvider.from_env()
    try:
        try:
            await run_ingest(
                title=title,
                material_path=material_path,
                db_path=db_path,
                provider=provider,
                approval=CliApprovalGate(console=console),
                console=console,
            )
        except ApprovalCancelled:
            console.print("[yellow]审批已取消，知识快照未变更。[/]")
    finally:
        await provider.aclose()
