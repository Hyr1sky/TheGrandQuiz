"""Article Workspace 的只读资源与文档投影。"""

from typing import Annotated, cast

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from grandquiz.domain.learning.document_search import DocumentSearch, ScopeResolutionError
from grandquiz.domain.learning.models import DocumentNode, DocumentNodeKind, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.domain.learning.store import ResourceStatus
from grandquiz.interfaces.api.errors import ApiError
from grandquiz.interfaces.api.runs import QuestionRequest, RunManager, RunView

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])


class ResourceSummary(BaseModel):
    resource_id: str
    url: str
    topic: str | None
    status: ResourceStatus
    trusted: bool
    current_revision_id: str | None

    @classmethod
    def from_resource(cls, resource: LearningResource) -> "ResourceSummary":
        return cls(
            resource_id=resource.resource_id,
            url=resource.url,
            topic=resource.topic,
            status=resource.status,
            trusted=resource.trusted,
            current_revision_id=resource.current_revision_id,
        )


class ResourceListResponse(BaseModel):
    items: list[ResourceSummary]


class DocumentNodeSummary(BaseModel):
    node_id: str
    revision_id: str
    parent_node_id: str | None
    kind: DocumentNodeKind
    ordinal: int
    depth: int
    title: str | None
    section_path: str
    synthetic: bool

    @classmethod
    def from_node(cls, node: DocumentNode) -> "DocumentNodeSummary":
        return cls.model_validate(node.model_dump(include=set(cls.model_fields)))


class DocumentOutlineResponse(BaseModel):
    resource_id: str
    nodes: list[DocumentNodeSummary]


class DocumentNodeReadResponse(BaseModel):
    resource_id: str
    revision_id: str
    node_id: str
    section_path: str
    start_offset: int
    end_offset: int
    content: str
    has_more: bool
    untrusted: bool


def persistence_from(request: Request) -> LearningPersistence:
    return cast("LearningPersistence", request.app.state.persistence)


def run_manager_from(request: Request) -> RunManager:
    return cast("RunManager", request.app.state.run_manager)


@router.get("", response_model=ResourceListResponse)
async def list_resources(request: Request) -> ResourceListResponse:
    resources = persistence_from(request).store.all_resources()
    return ResourceListResponse(
        items=[ResourceSummary.from_resource(resource) for resource in resources]
    )


@router.get("/{resource_id}/outline", response_model=DocumentOutlineResponse)
async def get_document_outline(
    resource_id: str,
    request: Request,
) -> DocumentOutlineResponse:
    store = persistence_from(request).store
    try:
        nodes = DocumentSearch(store).outline(resource_id)
    except ScopeResolutionError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message=f"资源不存在或没有当前版本：{resource_id}",
        ) from exc
    return DocumentOutlineResponse(
        resource_id=resource_id,
        nodes=[DocumentNodeSummary.from_node(node) for node in nodes],
    )


@router.get(
    "/{resource_id}/nodes/{node_id}",
    response_model=DocumentNodeReadResponse,
)
async def read_document_node(
    resource_id: str,
    node_id: str,
    request: Request,
    max_chars: Annotated[int, Query(ge=1, le=4_000)] = 2_000,
) -> DocumentNodeReadResponse:
    store = persistence_from(request).store
    try:
        result = DocumentSearch(store, turn_read_budget=max_chars).read_node(
            resource_id,
            node_id,
            max_chars=max_chars,
            budget_key="api-node-read",
        )
    except ScopeResolutionError as exc:
        raise ApiError(
            status_code=404,
            code="document_node_not_found",
            message=f"文档节点不存在：{resource_id}:{node_id}",
        ) from exc
    return DocumentNodeReadResponse(
        resource_id=result.resource_id,
        revision_id=result.revision_id,
        node_id=result.node_id,
        section_path=result.section_path,
        start_offset=result.start_offset,
        end_offset=result.end_offset,
        content=result.content,
        has_more=result.has_more,
        untrusted=result.untrusted,
    )


@router.post(
    "/{resource_id}/questions",
    response_model=RunView,
    status_code=202,
)
async def ask_resource_question(
    resource_id: str,
    question: QuestionRequest,
    request: Request,
) -> RunView:
    if persistence_from(request).store.current_revision(resource_id) is None:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message=f"资源不存在或没有当前版本：{resource_id}",
        )
    return run_manager_from(request).start_grounded_answer(
        resource_id=resource_id,
        request=question,
    )


@router.get("/{resource_id}", response_model=ResourceSummary)
async def get_resource(resource_id: str, request: Request) -> ResourceSummary:
    resource = persistence_from(request).store.get_resource(resource_id)
    if resource is None:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message=f"资源不存在：{resource_id}",
        )
    return ResourceSummary.from_resource(resource)
