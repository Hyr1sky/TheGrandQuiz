"""审批门原语——展示候选预览、返回获批子集（未获批的候选绝不入库，eval case 1）。

当前协议是同步批决策：Scripted adapter 供测试，CLI adapter 阻塞询问；两者都发
``approval.requested`` + ``approval.decided``。Web 的跨进程 suspend/resume 由
``AcquisitionLedger`` + API manager 持有，复用本模块的事件函数；同步 CLI 协议不冒充跨进程状态机。
审批事件是通用类型串，kernel 只做泛型分发。
"""

from collections.abc import Callable
from typing import Literal, Protocol

from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.kernel.events import EventEmitter

# kernel 不认识的通用审批事件类型串（见模块 docstring）。
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_DECIDED = "approval.decided"
ApprovalOutcome = Literal["approved", "rejected_all", "cancelled"]
ApprovalDecisionSource = Literal["scripted", "human_cli", "human_web"]


class ApprovalCancelled(RuntimeError):
    """用户取消审批；调用方不得用候选覆盖已有知识快照。"""


def emit_approval_requested(
    candidates: list[KnowledgeItem],
    *,
    emitter: EventEmitter,
    parent_span_id: str | None,
) -> None:
    """发最小审批预览，不把摘要、证据等额外内容写进 trace。"""
    emitter.emit(
        APPROVAL_REQUESTED,
        parent_span_id=parent_span_id,
        payload={"candidates": [{"item_id": c.item_id, "concept": c.concept} for c in candidates]},
    )


def emit_approval_decided(
    candidates: list[KnowledgeItem],
    approved: list[KnowledgeItem],
    *,
    outcome: ApprovalOutcome,
    decision_source: ApprovalDecisionSource,
    emitter: EventEmitter,
    parent_span_id: str | None,
) -> None:
    """把审批结果投影到事件脊柱；只记录身份与计数。"""
    emitter.emit(
        APPROVAL_DECIDED,
        parent_span_id=parent_span_id,
        payload={
            "outcome": outcome,
            "decision_source": decision_source,
            "candidate_count": len(candidates),
            "approved_count": len(approved),
            "approved_item_ids": [item.item_id for item in approved],
        },
    )


class ApprovalGate(Protocol):
    """同步审批协议：发 requested/decided 事件并返回获批子集。"""

    def request_approval(
        self,
        candidates: list[KnowledgeItem],
        *,
        emitter: EventEmitter,
        parent_span_id: str | None,
    ) -> list[KnowledgeItem]: ...


class ScriptedApprovalGate:
    """确定性审批门——注入 ``keep`` 谓词或 ``keep_ids`` 集合供测试脚本化决策。

    真实阻塞 CLI 交互由 ``interfaces.cli.approval.CliApprovalGate`` 提供；本类只服务确定性测试。
    两者都提供时 ``keep_ids`` 优先。
    """

    def __init__(
        self,
        *,
        keep: Callable[[KnowledgeItem], bool] | None = None,
        keep_ids: set[str] | None = None,
    ) -> None:
        if keep is None and keep_ids is None:
            raise ValueError("keep 与 keep_ids 至少提供其一")
        self._keep = keep
        self._keep_ids = keep_ids

    def request_approval(
        self,
        candidates: list[KnowledgeItem],
        *,
        emitter: EventEmitter,
        parent_span_id: str | None,
    ) -> list[KnowledgeItem]:
        emit_approval_requested(candidates, emitter=emitter, parent_span_id=parent_span_id)
        approved = [c for c in candidates if self._should_keep(c)]
        emit_approval_decided(
            candidates,
            approved,
            outcome="approved" if approved else "rejected_all",
            decision_source="scripted",
            emitter=emitter,
            parent_span_id=parent_span_id,
        )
        return approved

    def _should_keep(self, item: KnowledgeItem) -> bool:
        if self._keep_ids is not None:
            return item.item_id in self._keep_ids
        if self._keep is not None:
            return self._keep(item)
        return False  # __init__ 已保证不可达
