"""审批门原语——展示候选预览、返回获批子集（未获批的候选绝不入库，eval case 1）。

# SKELETON: 阻塞 CLI 交互形态见 docs/skeleton-ledger.md #3（正式=可恢复 turn）

接口形状第一天就按 suspend/resume 定：``request_approval`` 先发 ``approval.requested`` 事件
（含候选预览），再据决策返回保留子集——把它换成真正的挂起 / 恢复时，ingest 调用方不变。
``approval.requested`` 是 **kernel 级通用事件类型串**，kernel 不认识它（故不在 kernel 加常量，
仅在本 domain 模块留一个命名常量避免手抖拼错）。
"""

from collections.abc import Callable
from typing import Protocol

from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.kernel.events import EventEmitter

# kernel 不认识的通用审批事件类型串（见模块 docstring）。
APPROVAL_REQUESTED = "approval.requested"


class ApprovalGate(Protocol):
    """审批门协议：给一批候选，返回获批子集。实现须先发 ``approval.requested`` 事件。"""

    def request_approval(
        self,
        candidates: list[KnowledgeItem],
        *,
        emitter: EventEmitter,
        parent_span_id: str | None,
    ) -> list[KnowledgeItem]: ...


class ScriptedApprovalGate:
    """确定性审批门——注入 ``keep`` 谓词或 ``keep_ids`` 集合供测试脚本化决策。

    真实交互（阻塞 CLI / 可恢复 turn）是后续步骤；本类只提供协议的确定性实现。
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
        # 先发 approval.requested 点事件（parent=ingest span），payload 含候选预览。
        emitter.emit(
            APPROVAL_REQUESTED,
            parent_span_id=parent_span_id,
            payload={
                "candidates": [{"item_id": c.item_id, "concept": c.concept} for c in candidates]
            },
        )
        return [c for c in candidates if self._should_keep(c)]

    def _should_keep(self, item: KnowledgeItem) -> bool:
        if self._keep_ids is not None:
            return item.item_id in self._keep_ids
        if self._keep is not None:
            return self._keep(item)
        return False  # __init__ 已保证不可达
