"""ingest 编排——喂 URL → 深读 → 审批 → 入库的确定性 domain workflow（非自由 ReAct）。

ADR-0004："LLM 判卷，代码记账"。这里的骨架是确定性代码：状态转移、入库、发事件全在代码里，
LLM 只在 Reader 的"深读"一个槽被调用。每步都在**同一条事件脊柱**上发事件——trace 形状：

    ingest（根 span）
    └── model（Reader 的 model span，挂 ingest 下）
    · resource_created / resource_read / items_extracted / approval.requested / approval.decided /
      resource_approved / item_created … 皆 parent=ingest span 的点事件（无 span，不进树）

失败分两类：
- **领域失败**（fetch 失败、深读重试用尽拿不到合法输出）→ 标 ``failed`` + 发
  ``RESOURCE_FETCH_FAILED`` + 不产幽灵 item + 优雅返回（不 raise，eval case 7）。
- **基础设施 / harness 失败**（``ReplayMiss`` / provider 传输异常 / bug）→ 闭合 ingest
  span 后原样冒泡（不吞成 failed 以免掩盖 harness 错误；优雅降级属 M6 RecoveryPolicy）。
未获批候选绝不进 store（eval case 1，审批门返回子集 + 本编排只入库获批者共同保证）。
"""

import hashlib
from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.citations import CitationResolutionError, validate_exact_evidence
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest.fetch import FetchError, FetchSource, fetch_resource
from grandquiz.domain.learning.ingest.reader import (
    UNTRUSTED_READ_HOOK,
    Reader,
    ReaderError,
    ReaderEvidenceError,
    neutralize_fence,
)
from grandquiz.domain.learning.models import EvidenceLocator, KnowledgeItem, LearningResource
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.events import EventEmitter
from grandquiz.kernel.hooks import HookManager
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
    url: str,
    *,
    source: FetchSource,
    provider: Provider,
    store: Store,
    approval: ApprovalGate,
    emitter: EventEmitter,
    max_bytes: int,
    allowed_domains: Collection[str] | Literal["*"],
) -> IngestResult:
    """把一个 URL 喂入全局 KB，深读 → 审批 → 入库，全程发事件。见模块 docstring。

    ``resource_id`` 从稳定 locator 确定性派生（locator-addressed，ADR-0007）：同一 locator 重
    ingest 仍定位同一资源；审批成功后以原子快照提交切换 current revision（不按标题分库）。
    """
    # a. 开 ingest span（根）。此后任何未预期异常都必须闭合它（见末尾 except）。
    ingest_span = emitter.new_span_id()
    emitter.emit(
        _INGEST_STARTED,
        span_id=ingest_span,
        payload={"url": url},
    )
    resource = LearningResource.create(url=url)
    previous = store.get_resource(resource.resource_id)

    def fail(reason: str, *, classification: str | None = None) -> IngestResult:
        # 首次 ingest 失败留下 failed 诊断记录；刷新失败不覆盖既有获批 read 快照。
        if previous is None or previous.status != "read":
            failed = resource.model_copy(update={"status": "failed"})
            store.add_resource(failed)
        failure_payload = {"resource_id": resource.resource_id, "url": url, "reason": reason}
        if classification is not None:
            failure_payload["classification"] = classification
        emitter.emit(
            LearningEvent.RESOURCE_FETCH_FAILED,
            parent_span_id=ingest_span,
            payload=failure_payload,
        )
        emitter.emit(_INGEST_ENDED, span_id=ingest_span, payload={"ok": False, "reason": reason})
        return IngestResult(status="failed", resource_id=resource.resource_id, items=[])

    try:
        # b. 发 staged resource 事件；fetch / Reader / 审批完成前不覆盖既有获批快照。
        emitter.emit(
            LearningEvent.RESOURCE_CREATED,
            parent_span_id=ingest_span,
            payload=resource.model_dump(),
        )

        # c. fetch（守卫：域名 / 大小 / 源异常）。领域失败 → fail()（eval case 7）。
        try:
            fetched = await fetch_resource(
                url, source=source, max_bytes=max_bytes, allowed_domains=allowed_domains
            )
        except FetchError as exc:
            return fail(f"fetch: {exc}", classification=exc.reason)

        # d. 回填 staged 内容 + hash，status=read，trusted=False；RESOURCE_READ 让
        #    成功侧状态跃迁也上脊柱（对称于 RESOURCE_FETCH_FAILED，兑现"回放=事件流回放"）。
        content = fetched.content
        content_hash = fetched.content_hash
        resource = resource.model_copy(
            update={
                "raw_content": content,
                "content_hash": content_hash,
                "status": "read",
                "trusted": False,
            }
        )
        emitter.emit(
            LearningEvent.RESOURCE_READ,
            parent_span_id=ingest_span,
            payload={
                "resource_id": resource.resource_id,
                "status": resource.status,
                "content_hash": content_hash,
                "requested_url": fetched.requested_url,
                "final_url": fetched.final_url,
                "canonical_url": fetched.canonical_url,
                "title": fetched.title,
                "adapter": fetched.adapter,
                "extractor": fetched.extractor,
                "quality": fetched.quality.model_dump(),
            },
        )
        document = build_document_snapshot(resource)
        if document is None:  # pragma: no cover - fetch 成功保证 content/hash 同时存在
            raise RuntimeError("已读取资源缺少可解析的 content/hash")
        emitter.emit(
            LearningEvent.DOCUMENT_PARSED,
            parent_span_id=ingest_span,
            payload={
                "resource_id": resource.resource_id,
                "revision_id": document.revision.revision_id,
                "node_count": len(document.nodes),
                "synthetic_node_count": sum(node.synthetic for node in document.nodes),
            },
        )

        # e. Reader 深读。重试用尽拿不到合法输出（ReaderError）→ 领域失败 → fail()。
        #    provider 基础设施异常 / ReplayMiss 非领域失败 → 冒泡到外层 except。
        # 组装点：建 HookManager 并把注入中和注册到 UNTRUSTED_READ_HOOK 挂点，注入进 Reader（不在
        # reader 内 new 全局的）。行为等价于旧的内联直调 neutralize_fence——内容仍被中和后才喂 LLM。
        hooks = HookManager()
        hooks.register_interceptor(UNTRUSTED_READ_HOOK, neutralize_fence)
        reader = Reader(hooks=hooks)
        try:
            read_result = await reader.read_document(
                resource,
                document,
                provider=provider,
                emitter=emitter,
                parent_span_id=ingest_span,
            )
        except ReaderEvidenceError as exc:
            emitter.emit(
                LearningEvent.CITATION_REJECTED,
                parent_span_id=ingest_span,
                payload={
                    "revision_id": document.revision.revision_id,
                    "classification": exc.classification,
                    "quote_fingerprint": exc.public_fingerprint,
                },
            )
            return fail(f"reader evidence: {exc}")
        except ReaderError as exc:
            return fail(f"reader: {exc}")
        try:
            candidates = read_result.items
            validate_exact_evidence(document, candidates)
        except CitationResolutionError as exc:
            emitter.emit(
                LearningEvent.CITATION_REJECTED,
                parent_span_id=ingest_span,
                payload={
                    "revision_id": document.revision.revision_id,
                    "classification": exc.classification,
                },
            )
            return fail(f"grounding: {exc}")
        locators = [
            evidence.locator
            for candidate in candidates
            for evidence in candidate.evidence
            if isinstance(evidence.locator, EvidenceLocator)
        ]
        emitter.emit(
            LearningEvent.CITATION_VALIDATED,
            parent_span_id=ingest_span,
            payload={
                "revision_id": document.revision.revision_id,
                "node_ids": sorted({locator.node_id for locator in locators}),
                "evidence_count": len(locators),
                "evidence_fingerprint": hashlib.sha256(
                    "\0".join(sorted(locator.quote_hash for locator in locators)).encode("utf-8")
                ).hexdigest(),
            },
        )

        # 深读成功 → 把资源级 topic（RAG-metadata）写入 staged resource。
        # 深读失败（failed 分支）绝不到这里，故失败资源 topic 恒 None（保持契约）。topic 是目录式
        # scope 清单的人类可读来源（GKB-S3）。此步不发新事件——RESOURCE_READ 已上脊柱、事件序不变。
        resource = resource.model_copy(update={"topic": read_result.topic})
        emitter.emit(
            LearningEvent.ITEMS_EXTRACTED,
            parent_span_id=ingest_span,
            payload={
                "resource_id": resource.resource_id,
                "candidates": [{"item_id": c.item_id, "concept": c.concept} for c in candidates],
            },
        )

        # f. 审批门：内部发 requested/decided 两个事件，再返回获批子集。
        approved = approval.request_approval(
            candidates, emitter=emitter, parent_span_id=ingest_span
        )

        # g. 审批完成后一次原子替换 resource revision + 获批 KnowledgeItem 快照。
        store.replace_snapshot(resource, approved)
        committed_revision = store.current_revision(resource.resource_id)
        if (
            committed_revision is None
            or committed_revision.revision_id != document.revision.revision_id
        ):
            raise RuntimeError("获批 revision/tree 未成为当前快照")
        emitter.emit(
            LearningEvent.REVISION_COMMITTED,
            parent_span_id=ingest_span,
            payload={
                "resource_id": resource.resource_id,
                "revision_id": committed_revision.revision_id,
                "node_count": len(document.nodes),
            },
        )
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
