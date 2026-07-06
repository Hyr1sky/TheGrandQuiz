"""ingest 编排——喂 URL → 深读 → 审批 → 入库的确定性 domain workflow（非自由 ReAct）。

ADR-0004："LLM 判卷，代码记账"。这里的骨架是确定性代码：状态转移、入库、发事件全在代码里，
LLM 只在 Reader 的"深读"一个槽被调用。每步都在**同一条事件脊柱**上发事件——trace 形状：

    ingest（根 span）
    └── model（Reader 的 model span，挂 ingest 下）
    · resource_created / resource_read / items_extracted / approval.requested /
      resource_approved / item_created … 皆 parent=ingest span 的点事件（无 span，不进树）

失败分两类：
- **领域失败**（fetch 失败、深读重试用尽拿不到合法输出）→ 标 ``failed`` + 发
  ``RESOURCE_FETCH_FAILED`` + 不产幽灵 item + 优雅返回（不 raise，eval case 7）。
- **基础设施 / harness 失败**（``ReplayMiss`` / provider 传输异常 / bug）→ 闭合 ingest
  span 后原样冒泡（不吞成 failed 以免掩盖 harness 错误；优雅降级属 M6 RecoveryPolicy）。
未获批候选绝不进 store（eval case 1，审批门返回子集 + 本编排只入库获批者共同保证）。
"""

from collections.abc import Callable, Collection
from typing import Literal

from pydantic import BaseModel

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.fetch import FetchError, fetch_resource
from grandquiz.domain.learning.models import KnowledgeItem, LearningResource, LearningTask
from grandquiz.domain.learning.reader import Reader, ReaderError
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.events import EventEmitter
from grandquiz.providers.base import Provider

# ingest 是 workflow span，用 kernel 级通用类型串（kernel 不认识 "ingest"，泛型建树即可）。
_INGEST_STARTED = "ingest.started"
_INGEST_ENDED = "ingest.ended"


class IngestResult(BaseModel):
    """一次 ingest 的结果：状态 + 资源 id + 获批入库的 item 列表。"""

    status: Literal["read", "failed"]
    resource_id: str
    items: list[KnowledgeItem]


async def ingest_resource(
    task: LearningTask,
    url: str,
    *,
    source: Callable[[str], str],
    provider: Provider,
    store: Store,
    approval: ApprovalGate,
    emitter: EventEmitter,
    max_bytes: int,
    allowed_domains: Collection[str],
) -> IngestResult:
    """把一个 URL 喂入某 LearningTask，深读 → 审批 → 入库，全程发事件。见模块 docstring。"""
    # a. 开 ingest span（根）。此后任何未预期异常都必须闭合它（见末尾 except）。
    ingest_span = emitter.new_span_id()
    emitter.emit(
        _INGEST_STARTED,
        span_id=ingest_span,
        payload={"task_id": task.task_id, "url": url},
    )
    resource = LearningResource.create(task_id=task.task_id, url=url)

    def fail(reason: str) -> IngestResult:
        # 领域失败分支（fetch / Reader 共用）：标 failed、闭合 span、优雅返回（不 raise，case 7）。
        store.set_resource_status(resource.resource_id, "failed")
        emitter.emit(
            LearningEvent.RESOURCE_FETCH_FAILED,
            parent_span_id=ingest_span,
            payload={"resource_id": resource.resource_id, "url": url, "reason": reason},
        )
        emitter.emit(_INGEST_ENDED, span_id=ingest_span, payload={"ok": False, "reason": reason})
        return IngestResult(status="failed", resource_id=resource.resource_id, items=[])

    try:
        # b. 建 task（幂等）+ resource，发 RESOURCE_CREATED。
        store.add_task(task)
        store.add_resource(resource)
        emitter.emit(
            LearningEvent.RESOURCE_CREATED,
            parent_span_id=ingest_span,
            payload=resource.model_dump(),
        )

        # c. fetch（守卫：域名 / 大小 / 源异常）。领域失败 → fail()（eval case 7）。
        try:
            content, content_hash = fetch_resource(
                url, source=source, max_bytes=max_bytes, allowed_domains=allowed_domains
            )
        except FetchError as exc:
            return fail(f"fetch: {exc}")

        # d. 回填内容 + hash，status=read，trusted=False（持久化，日后不重抓）；RESOURCE_READ 让
        #    成功侧状态跃迁也上脊柱（对称于 RESOURCE_FETCH_FAILED，兑现"回放=事件流回放"）。
        resource = resource.model_copy(
            update={
                "raw_content": content,
                "content_hash": content_hash,
                "status": "read",
                "trusted": False,
            }
        )
        store.add_resource(resource)
        emitter.emit(
            LearningEvent.RESOURCE_READ,
            parent_span_id=ingest_span,
            payload={
                "resource_id": resource.resource_id,
                "status": resource.status,
                "content_hash": content_hash,
            },
        )

        # e. Reader 深读。重试用尽拿不到合法输出（ReaderError）→ 领域失败 → fail()。
        #    provider 基础设施异常 / ReplayMiss 非领域失败 → 冒泡到外层 except。
        reader = Reader()
        try:
            candidates = await reader.read(
                resource,
                content,
                provider=provider,
                emitter=emitter,
                parent_span_id=ingest_span,
            )
        except ReaderError as exc:
            return fail(f"reader: {exc}")

        emitter.emit(
            LearningEvent.ITEMS_EXTRACTED,
            parent_span_id=ingest_span,
            payload={
                "resource_id": resource.resource_id,
                "candidates": [{"item_id": c.item_id, "concept": c.concept} for c in candidates],
            },
        )

        # f. 审批门：内部先发 approval.requested，再返回获批子集。
        approved = approval.request_approval(
            candidates, emitter=emitter, parent_span_id=ingest_span
        )

        # g. 只入库获批者（eval case 1：未获批不进 store），发 approved / item_created。
        store.add_items(approved)
        emitter.emit(
            LearningEvent.RESOURCE_APPROVED,
            parent_span_id=ingest_span,
            payload={
                "resource_id": resource.resource_id,
                "approved_item_ids": [item.item_id for item in approved],
            },
        )
        for item in approved:
            emitter.emit(
                LearningEvent.ITEM_CREATED,
                parent_span_id=ingest_span,
                payload=item.model_dump(),
            )

        # h. 闭合 ingest span。
        emitter.emit(
            _INGEST_ENDED, span_id=ingest_span, payload={"ok": True, "item_count": len(approved)}
        )
        return IngestResult(status="read", resource_id=resource.resource_id, items=approved)
    except Exception as exc:
        # 非领域异常（ReplayMiss / provider 基础设施错误 / bug）：闭合 ingest span 后原样冒泡。
        # 不吞成 "failed"——那会掩盖 harness / 基础设施错误；优雅降级的恢复语义属 M6 RecoveryPolicy。
        emitter.emit(_INGEST_ENDED, span_id=ingest_span, payload={"ok": False, "error": repr(exc)})
        raise
