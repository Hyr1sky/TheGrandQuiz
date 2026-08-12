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
from contextlib import nullcontext
from typing import Literal, Protocol, cast

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
from grandquiz.domain.learning.persistence import LearningDatabase
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.events import EventEmitter
from grandquiz.kernel.hooks import HookManager
from grandquiz.providers.base import Provider

# ingest 是 workflow span，用 kernel 级通用类型串（kernel 不认识 "ingest"，泛型建树即可）。
_INGEST_STARTED = "ingest.started"
_INGEST_ENDED = "ingest.ended"


class IngestClassificationRepository(Protocol):
    """获批快照提交时所需的最小分类写入端口。"""

    @property
    def transaction_owner(self) -> LearningDatabase: ...

    def record_ingest_proposal(
        self,
        item: KnowledgeItem,
        *,
        ingest_id: str,
        trace_id: str,
    ) -> None: ...


class IngestRecognitionLexiconProjection(Protocol):
    """获批 snapshot 提交后，同事务重建 revision 词表所需的最小 Interface。"""

    @property
    def transaction_owner(self) -> LearningDatabase: ...

    def rebuild_revision(self, revision_id: str) -> object: ...


IngestFailureStage = Literal["fetch", "reader", "evidence_validation"]
IngestFailureCode = Literal[
    "invalid_url",
    "domain_not_allowed",
    "too_large",
    "ssrf",
    "redirect_limit",
    "timeout",
    "http_status",
    "unsupported_content_type",
    "empty_content",
    "too_short",
    "navigation_page",
    "login_page",
    "bot_challenge",
    "source_failure",
    "quote_mismatch",
    "unknown_node",
    "span_out_of_bounds",
    "evidence_schema",
    "reader_failed",
    "citation_invalid",
    "ingest_failed",
]

_PUBLIC_FAILURE_REASONS = {
    "invalid_url": "材料地址无效",
    "domain_not_allowed": "材料地址不在允许范围内",
    "too_large": "材料内容超过允许大小",
    "ssrf": "材料地址未通过网络安全检查",
    "redirect_limit": "材料地址重定向次数过多",
    "timeout": "材料抓取超时",
    "http_status": "材料页面返回异常状态",
    "unsupported_content_type": "材料格式暂不支持",
    "quote_mismatch": "Evidence 引文无法精确定位到原文节点",
    "unknown_node": "Evidence 引用了当前批次不存在的文档节点",
    "span_out_of_bounds": "Evidence 定位超出原文节点边界",
    "evidence_schema": "Evidence 输出不符合结构化契约",
    "login_page": "目标页面需要登录，无法安全读取正文",
    "bot_challenge": "目标页面要求人机验证，无法安全读取正文",
    "navigation_page": "目标页面主要是导航内容，未发现可入库正文",
    "empty_content": "目标页面没有可入库正文",
    "too_short": "目标页面正文过短，无法可靠入库",
    "reader_failed": "材料深读未返回合法结果",
    "citation_invalid": "Evidence 未通过精确原文校验",
    "source_failure": "材料抓取失败，请检查地址或页面内容",
    "ingest_failed": "材料读取或深读失败，请检查内容后重试",
}
_KNOWN_FAILURE_CODES = frozenset(_PUBLIC_FAILURE_REASONS)
_FALLBACK_CODE_BY_STAGE: dict[IngestFailureStage, IngestFailureCode] = {
    "fetch": "source_failure",
    "reader": "reader_failed",
    "evidence_validation": "citation_invalid",
}


def public_ingest_failure_reason(code: IngestFailureCode) -> str:
    """返回可进入 CLI/Web 的固定失败文案，不接受任意内部 detail。"""

    return _PUBLIC_FAILURE_REASONS[code]


class IngestFailure(BaseModel):
    """可安全投影到 interface 的稳定领域失败信封。"""

    code: IngestFailureCode
    stage: IngestFailureStage
    reason: str


def _public_failure(
    code: str,
    *,
    stage: IngestFailureStage,
    fallback: str,
) -> IngestFailure:
    public_code = (
        cast("IngestFailureCode", code)
        if code in _KNOWN_FAILURE_CODES
        else _FALLBACK_CODE_BY_STAGE[stage]
    )
    return IngestFailure(
        code=public_code,
        stage=stage,
        reason=_PUBLIC_FAILURE_REASONS.get(public_code, fallback),
    )


class IngestResult(BaseModel):
    """一次 ingest 的结果：状态 + 资源 id + 获批入库的 item 列表。"""

    status: Literal["read", "failed"]
    resource_id: str
    items: list[KnowledgeItem]
    failure: IngestFailure | None = None


class PreparedIngest(BaseModel):
    """已抓取、深读并通过证据门，但尚未进入知识库的可持久化快照。"""

    resource: LearningResource
    candidates: list[KnowledgeItem]
    revision_id: str
    node_count: int
    ingest_span_id: str


async def prepare_ingest(
    url: str,
    *,
    source: FetchSource,
    provider: Provider,
    store: Store,
    emitter: EventEmitter,
    max_bytes: int,
    allowed_domains: Collection[str] | Literal["*"],
    persist_failed_resource: bool = True,
) -> PreparedIngest | IngestResult:
    """抓取并深读材料，返回可审批快照；成功时绝不修改知识库。

    ``resource_id`` 从稳定 locator 确定性派生（locator-addressed，ADR-0007）：同一 locator 重
    ingest 仍定位同一资源。``persist_failed_resource=False`` 供 Web 管理态使用，确保失败或取消
    不留下半成品；CLI/ReAct 的兼容入口保留既有失败诊断记录语义。
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

    def fail(
        detail: str,
        *,
        failure: IngestFailure,
    ) -> IngestResult:
        # 首次 ingest 失败留下 failed 诊断记录；刷新失败不覆盖既有获批 read 快照。
        if persist_failed_resource and (previous is None or previous.status != "read"):
            failed = resource.model_copy(update={"status": "failed"})
            store.add_resource(failed)
        failure_payload = {
            "resource_id": resource.resource_id,
            "url": url,
            "code": failure.code,
            "stage": failure.stage,
            "reason": failure.reason,
            "detail": detail,
            "classification": failure.code,
        }
        emitter.emit(
            LearningEvent.RESOURCE_FETCH_FAILED,
            parent_span_id=ingest_span,
            payload=failure_payload,
        )
        emitter.emit(
            _INGEST_ENDED,
            span_id=ingest_span,
            payload={
                "ok": False,
                "code": failure.code,
                "stage": failure.stage,
                "reason": failure.reason,
            },
        )
        return IngestResult(
            status="failed",
            resource_id=resource.resource_id,
            items=[],
            failure=failure,
        )

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
            return fail(
                f"fetch: {exc}",
                failure=_public_failure(
                    exc.reason,
                    stage="fetch",
                    fallback="材料抓取失败，请检查地址或页面内容",
                ),
            )

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
            return fail(
                f"reader evidence: {exc}",
                failure=_public_failure(
                    exc.classification,
                    stage="evidence_validation",
                    fallback="Evidence 无法精确定位到原文",
                ),
            )
        except ReaderError as exc:
            return fail(
                f"reader: {exc}",
                failure=_public_failure(
                    "reader_failed",
                    stage="reader",
                    fallback="材料深读未返回合法结果",
                ),
            )
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
            return fail(
                f"grounding: {exc}",
                failure=_public_failure(
                    exc.classification or "citation_invalid",
                    stage="evidence_validation",
                    fallback="Evidence 未通过精确原文校验",
                ),
            )
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

        return PreparedIngest(
            resource=resource,
            candidates=candidates,
            revision_id=document.revision.revision_id,
            node_count=len(document.nodes),
            ingest_span_id=ingest_span,
        )
    except Exception as exc:
        # 非领域异常（ReplayMiss / provider 基础设施错误 / bug）：闭合 ingest span 后原样冒泡。
        # 不吞成 "failed"——那会掩盖 harness / 基础设施错误；优雅降级的恢复语义属 M6 RecoveryPolicy。
        emitter.emit(_INGEST_ENDED, span_id=ingest_span, payload={"ok": False, "error": repr(exc)})
        raise


def persist_prepared_ingest(
    prepared: PreparedIngest,
    *,
    approved: list[KnowledgeItem],
    store: Store,
    classifications: IngestClassificationRepository | None = None,
    lexicons: IngestRecognitionLexiconProjection | None = None,
    trace_id: str | None = None,
) -> IngestResult:
    """只提交获批快照，不发事件；调用方可把它纳入更大的原子事务。"""
    candidate_ids = {item.item_id for item in prepared.candidates}
    if any(item.item_id not in candidate_ids for item in approved):
        raise ValueError("approved 必须是 prepared candidates 的子集")

    if classifications is not None and trace_id is None:
        raise ValueError("写入分类 proposal 时必须提供 trace_id")
    owners = [
        participant.transaction_owner
        for participant in (classifications, lexicons)
        if participant is not None
    ]
    if owners and any(owner is not owners[0] for owner in owners[1:]):
        raise ValueError("分类与 RecognitionLexicon 必须共享同一 LearningDatabase")
    transaction = nullcontext() if not owners else owners[0].transaction()
    with transaction:
        store.replace_snapshot(prepared.resource, approved)
        committed_revision = store.current_revision(prepared.resource.resource_id)
        if committed_revision is None or committed_revision.revision_id != prepared.revision_id:
            raise RuntimeError("获批 revision/tree 未成为当前快照")
        if classifications is not None:
            assert trace_id is not None
            for item in approved:
                classifications.record_ingest_proposal(
                    item,
                    ingest_id=prepared.revision_id,
                    trace_id=trace_id,
                )
        if lexicons is not None:
            lexicons.rebuild_revision(prepared.revision_id)
    return IngestResult(
        status="read",
        resource_id=prepared.resource.resource_id,
        items=approved,
    )


def emit_prepared_ingest_committed(
    prepared: PreparedIngest,
    result: IngestResult,
    *,
    emitter: EventEmitter,
) -> None:
    """持久事务提交后，把已经成立的 ingest 事实写入事件脊柱。"""
    emitter.emit(
        LearningEvent.REVISION_COMMITTED,
        parent_span_id=prepared.ingest_span_id,
        payload={
            "resource_id": prepared.resource.resource_id,
            "revision_id": prepared.revision_id,
            "node_count": prepared.node_count,
        },
    )
    emitter.emit(
        LearningEvent.RESOURCE_APPROVED,
        parent_span_id=prepared.ingest_span_id,
        payload={
            "resource_id": prepared.resource.resource_id,
            "approved_item_ids": [item.item_id for item in result.items],
        },
    )
    for item in result.items:
        emitter.emit(
            LearningEvent.ITEM_CREATED,
            parent_span_id=prepared.ingest_span_id,
            payload=item.model_dump(),
        )
    emitter.emit(
        _INGEST_ENDED,
        span_id=prepared.ingest_span_id,
        payload={"ok": True, "item_count": len(result.items)},
    )


def commit_prepared_ingest(
    prepared: PreparedIngest,
    *,
    approved: list[KnowledgeItem],
    store: Store,
    emitter: EventEmitter,
    classifications: IngestClassificationRepository | None = None,
    lexicons: IngestRecognitionLexiconProjection | None = None,
) -> IngestResult:
    """兼容同步入口：提交获批快照，并在提交成功后闭合 ingest span。"""
    result = persist_prepared_ingest(
        prepared,
        approved=approved,
        store=store,
        classifications=classifications,
        lexicons=lexicons,
        trace_id=emitter.trace_id,
    )
    emit_prepared_ingest_committed(prepared, result, emitter=emitter)
    return result


def abort_ingest(
    ingest_span_id: str,
    *,
    reason: str,
    emitter: EventEmitter,
) -> None:
    """取消或进程中断时闭合尚未提交的 ingest span；不触碰知识库。"""
    emitter.emit(
        _INGEST_ENDED,
        span_id=ingest_span_id,
        payload={"ok": False, "reason": reason},
    )


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
    classifications: IngestClassificationRepository | None = None,
    lexicons: IngestRecognitionLexiconProjection | None = None,
) -> IngestResult:
    """兼容 CLI/ReAct 的单次工作流：准备 → 同步审批 → 原子提交。"""
    prepared = await prepare_ingest(
        url,
        source=source,
        provider=provider,
        store=store,
        emitter=emitter,
        max_bytes=max_bytes,
        allowed_domains=allowed_domains,
    )
    if isinstance(prepared, IngestResult):
        return prepared
    approved = approval.request_approval(
        prepared.candidates,
        emitter=emitter,
        parent_span_id=prepared.ingest_span_id,
    )
    return commit_prepared_ingest(
        prepared,
        approved=approved,
        store=store,
        emitter=emitter,
        classifications=classifications,
        lexicons=lexicons,
    )
