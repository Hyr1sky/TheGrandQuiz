"""终端阻塞审批门：展示 Reader 候选并逐项保留或剔除。"""

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from grandquiz.domain.learning.approval import (
    ApprovalCancelled,
    emit_approval_decided,
    emit_approval_requested,
)
from grandquiz.domain.learning.models import EvidenceLocator, KnowledgeItem
from grandquiz.kernel.events import EventEmitter

InputFn = Callable[[str], str]


class CliApprovalGate:
    """生产 CLI 的同步审批 adapter；可替换输入函数以做确定性测试。"""

    def __init__(self, *, console: Console, input_fn: InputFn = input) -> None:
        self._console = console
        self._input = input_fn

    def request_approval(
        self,
        candidates: list[KnowledgeItem],
        *,
        emitter: EventEmitter,
        parent_span_id: str | None,
    ) -> list[KnowledgeItem]:
        emit_approval_requested(candidates, emitter=emitter, parent_span_id=parent_span_id)
        approved: list[KnowledgeItem] = []
        try:
            for index, item in enumerate(candidates, start=1):
                self._render_candidate(item, index=index, total=len(candidates))
                if self._ask_keep():
                    approved.append(item)
        except (EOFError, KeyboardInterrupt, ApprovalCancelled):
            emit_approval_decided(
                candidates,
                [],
                outcome="cancelled",
                decision_source="human_cli",
                emitter=emitter,
                parent_span_id=parent_span_id,
            )
            raise ApprovalCancelled("用户取消审批") from None

        outcome = "approved" if approved else "rejected_all"
        emit_approval_decided(
            candidates,
            approved,
            outcome=outcome,
            decision_source="human_cli",
            emitter=emitter,
            parent_span_id=parent_span_id,
        )
        if approved:
            self._console.print(
                f"[green]审批完成：保留 {len(approved)}/{len(candidates)} 个知识点。[/]"
            )
        else:
            self._console.print("[yellow]审批完成：未保留任何知识点。[/]")
        return approved

    def _render_candidate(self, item: KnowledgeItem, *, index: int, total: int) -> None:
        self._console.print(f"\n[bold]候选 {index}/{total}：{escape(item.concept)}[/]")
        self._console.print(f"摘要：{escape(item.summary)}")
        self._console.print(f"置信度：{item.confidence:.2f}")
        self._console.print("证据：")
        for evidence in item.evidence:
            if isinstance(evidence.locator, EvidenceLocator):
                label = evidence.locator.section_path or "文档根"
            else:
                label = evidence.locator
            locator = f" ({escape(label)})" if label else ""
            self._console.print(f"  - {escape(evidence.quote)}{locator}")

    def _ask_keep(self) -> bool:
        while True:
            reply = self._input("保留该知识点？[Y/n/q] ").strip().lower()
            if reply in {"", "y", "yes"}:
                return True
            if reply in {"n", "no"}:
                return False
            if reply in {"q", "quit", "cancel"}:
                raise ApprovalCancelled("用户取消审批")
            self._console.print("[yellow]请输入 y、n 或 q。[/]")
