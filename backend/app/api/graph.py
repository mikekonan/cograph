from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import get_current_user_optional, get_db_session, get_settings_dep
from backend.app.core.errors import ApiError
from backend.app.core.repository_access import get_readable_repository_by_slug
from backend.app.graph.queries import GraphQueryFilters, GraphQueryService, GraphView
from backend.app.models.enums import CodeNodeType, RepositoryStatus
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile
from backend.app.models.user import User

router = APIRouter(prefix="/repos", tags=["repos"])

_graph_query_service = GraphQueryService()


class SourceFileResponse(BaseModel):
    id: UUID
    repository_id: UUID
    file_path: str
    language: str
    kind: str
    content: str
    content_hash: str
    bytes: int
    commit_sha: str | None


class SourceFileRangeResponse(BaseModel):
    id: UUID
    file_path: str
    start_byte: int
    end_byte: int
    content: str
    bytes: int


class GraphNodeResponse(BaseModel):
    id: UUID
    name: str
    node_type: CodeNodeType
    language: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    complexity: int
    parent_name: str | None


class GraphEdgeResponse(BaseModel):
    source: UUID
    target: UUID
    type: str


class GraphStatsResponse(BaseModel):
    total_nodes: int
    matched_nodes: int
    returned_nodes: int
    languages: dict[str, int]


class GraphListResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    stats: GraphStatsResponse


class GraphRelatedNodeResponse(BaseModel):
    id: UUID
    name: str
    node_type: CodeNodeType
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None


class GraphParentResponse(BaseModel):
    id: UUID
    name: str
    node_type: CodeNodeType


class GraphNodeDetailResponse(BaseModel):
    id: UUID
    name: str
    node_type: CodeNodeType
    language: str
    file_path: str
    start_line: int
    end_line: int
    content: str
    signature: str | None
    doc_comment: str | None
    metadata: dict[str, object]
    complexity: int
    callers: list[GraphRelatedNodeResponse]
    callees: list[GraphRelatedNodeResponse]
    members: list[GraphRelatedNodeResponse]
    parent: GraphParentResponse | None


_FE_NODE_TYPES: frozenset[str] = frozenset(
    {"function", "class", "method", "interface", "struct", "module"}
)
_EXCLUDED_EDGE_TYPES: frozenset[str] = frozenset({"declares"})


@router.get("/{host}/{owner}/{name}/graph", response_model=GraphListResponse)
async def get_repository_graph(
    host: str,
    owner: str,
    name: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
    view: GraphView = Query(default=GraphView.ARCHITECTURE),
    node_type: CodeNodeType | None = Query(default=None),
    language: str | None = Query(default=None),
    module: str | None = Query(default=None),
    search: str | None = Query(default=None),
    depth: int = Query(default=2, ge=1, le=10),
    limit: int = Query(default=200, ge=1, le=200),
) -> GraphListResponse:
    del depth  # Reserved for recursive traversal when graph queries grow beyond flat filtering.

    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if repository.status is not RepositoryStatus.READY:
        # Repo is still indexing — return an empty graph so FE shows the
        # "indexing in progress" placeholder instead of an error banner.
        return GraphListResponse(
            nodes=[],
            edges=[],
            stats=GraphStatsResponse(
                total_nodes=0,
                matched_nodes=0,
                returned_nodes=0,
                languages={},
            ),
        )

    result = await _graph_query_service.list_graph(
        session=session,
        repository_id=repository.id,
        filters=GraphQueryFilters(
            view=view,
            node_type=node_type,
            language=language,
            module=module,
            search=search,
            limit=limit,
        ),
    )
    # Filter to only the node types and edge types the FE union supports.
    filtered_nodes = [
        node for node in result.nodes
        if (node.node_type.value if isinstance(node.node_type, CodeNodeType) else str(node.node_type))
        in _FE_NODE_TYPES
    ]
    filtered_node_ids = {n.id for n in filtered_nodes}
    filtered_edges = [
        edge for edge in result.edges
        if (edge.edge_type.value if hasattr(edge.edge_type, "value") else str(edge.edge_type))
        not in _EXCLUDED_EDGE_TYPES
        and edge.source in filtered_node_ids
        and edge.target in filtered_node_ids
    ]
    return GraphListResponse(
        nodes=[
            GraphNodeResponse(
                id=node.id,
                name=node.name,
                node_type=node.node_type,
                language=node.language,
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
                signature=node.signature,
                complexity=0,
                parent_name=node.parent_name,
            )
            for node in filtered_nodes
        ],
        edges=[
            GraphEdgeResponse(
                source=edge.source,
                target=edge.target,
                type=edge.edge_type.value,
            )
            for edge in filtered_edges
        ],
        stats=GraphStatsResponse(
            total_nodes=result.stats.total_nodes,
            matched_nodes=result.stats.matched_nodes,
            returned_nodes=result.stats.returned_nodes,
            languages=result.stats.languages,
        ),
    )


class GraphNodesCheckRequest(BaseModel):
    """Bulk staleness check for a list of code-node UUIDs.

    Used by the wiki page metadata panel: pages persist UUID-bearing
    citation hrefs, and re-indexes can drop nodes when their underlying
    symbol was renamed/removed/moved. The FE batches all `kind=node`
    citation IDs on a page into one request to compute a stale-count
    chip without N round-trips.
    """

    node_ids: list[UUID]


class GraphNodesCheckResponse(BaseModel):
    ok: list[UUID]
    stale: list[UUID]


@router.post(
    "/{host}/{owner}/{name}/graph/nodes/check",
    response_model=GraphNodesCheckResponse,
)
async def check_repository_graph_nodes(
    host: str,
    owner: str,
    name: str,
    payload: GraphNodesCheckRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> GraphNodesCheckResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if not payload.node_ids:
        return GraphNodesCheckResponse(ok=[], stale=[])
    existing = await _graph_query_service.existing_node_ids(
        session=session,
        repository_id=repository.id,
        node_ids=payload.node_ids,
    )
    ok = [nid for nid in payload.node_ids if nid in existing]
    stale = [nid for nid in payload.node_ids if nid not in existing]
    return GraphNodesCheckResponse(ok=ok, stale=stale)


@router.get(
    "/{host}/{owner}/{name}/graph/nodes/by-qn/{qualified_name:path}",
    response_model=GraphNodeDetailResponse,
)
async def get_repository_graph_node_by_qualified_name(
    host: str,
    owner: str,
    name: str,
    qualified_name: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> GraphNodeDetailResponse:
    """Resolve a code node by its `qualified_name` for the current repo.

    Used by the wiki citation fallback path. Persisted markdown carries
    `?node=<uuid>` hrefs frozen at generation time. When a re-index moves
    or renames the underlying symbol, the UUID 404s; the FE retries here
    with the citation's `qualified_name` to pick up the new UUID
    transparently. Returns 404 only when the symbol is genuinely gone.
    """
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    node_id = await _graph_query_service.resolve_node_id_by_qualified_name(
        session=session,
        repository_id=repository.id,
        qualified_name=qualified_name,
    )
    if node_id is None:
        raise ApiError(404, "NOT_FOUND", "Graph node not found")
    node = await _graph_query_service.get_node_detail(
        session=session,
        repository_id=repository.id,
        node_id=node_id,
    )
    if node is None:
        raise ApiError(404, "NOT_FOUND", "Graph node not found")
    node_type_val = (
        node.node_type.value if isinstance(node.node_type, CodeNodeType) else str(node.node_type)
    )
    if node_type_val not in _FE_NODE_TYPES:
        raise ApiError(404, "NOT_FOUND", "Graph node not found")
    return _node_detail_response(node)


@router.get("/{host}/{owner}/{name}/graph/nodes/{node_id}", response_model=GraphNodeDetailResponse)
async def get_repository_graph_node(
    host: str,
    owner: str,
    name: str,
    node_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> GraphNodeDetailResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    node = await _graph_query_service.get_node_detail(
        session=session,
        repository_id=repository.id,
        node_id=node_id,
    )
    if node is None:
        raise ApiError(404, "NOT_FOUND", "Graph node not found")
    node_type_val = (
        node.node_type.value if isinstance(node.node_type, CodeNodeType) else str(node.node_type)
    )
    if node_type_val not in _FE_NODE_TYPES:
        raise ApiError(404, "NOT_FOUND", "Graph node not found")

    return _node_detail_response(node)


def _node_detail_response(node) -> GraphNodeDetailResponse:
    return GraphNodeDetailResponse(
        id=node.id,
        name=node.name,
        node_type=node.node_type,
        language=node.language,
        file_path=node.file_path,
        start_line=node.start_line,
        end_line=node.end_line,
        content=node.content,
        signature=node.signature,
        doc_comment=node.doc_comment,
        metadata=node.metadata,
        complexity=0,
        callers=[_related_node_response(related) for related in node.callers],
        callees=[_related_node_response(related) for related in node.callees],
        members=[_related_node_response(related) for related in node.members],
        parent=(
            GraphParentResponse(
                id=node.parent.id,
                name=node.parent.name,
                node_type=node.parent.node_type,
            )
            if node.parent is not None
            else None
        ),
    )


def _related_node_response(related) -> GraphRelatedNodeResponse:
    return GraphRelatedNodeResponse(
        id=related.id,
        name=related.name,
        node_type=related.node_type,
        file_path=related.file_path,
        start_line=related.start_line,
        end_line=related.end_line,
        signature=related.signature,
    )


@router.get(
    "/{host}/{owner}/{name}/files/{source_file_id}",
    response_model=SourceFileResponse,
)
async def get_repository_source_file(
    host: str,
    owner: str,
    name: str,
    source_file_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> SourceFileResponse:
    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    source_file = await session.scalar(
        select(SourceFile).where(
            SourceFile.id == source_file_id,
            SourceFile.repository_id == repository.id,
        )
    )
    if source_file is None:
        raise ApiError(404, "NOT_FOUND", "Source file not found")

    try:
        content = bytes(source_file.raw_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(500, "SOURCE_FILE_DECODE_FAILED", str(exc)) from exc

    return SourceFileResponse(
        id=source_file.id,
        repository_id=source_file.repository_id,
        file_path=source_file.file_path,
        language=source_file.language,
        kind=source_file.kind,
        content=content,
        content_hash=source_file.content_hash,
        bytes=source_file.bytes,
        commit_sha=source_file.commit_sha,
    )


@router.get(
    "/{host}/{owner}/{name}/files/{source_file_id}/range",
    response_model=SourceFileRangeResponse,
)
async def get_repository_source_file_range(
    host: str,
    owner: str,
    name: str,
    source_file_id: UUID,
    start: int = Query(..., ge=0, description="Inclusive start byte offset"),
    end: int = Query(..., ge=0, description="Exclusive end byte offset"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> SourceFileRangeResponse:
    if end < start:
        raise ApiError(400, "INVALID_RANGE", "end must be >= start")

    repository = await _require_ready_repository(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    source_file = await session.scalar(
        select(SourceFile).where(
            SourceFile.id == source_file_id,
            SourceFile.repository_id == repository.id,
        )
    )
    if source_file is None:
        raise ApiError(404, "NOT_FOUND", "Source file not found")

    raw = bytes(source_file.raw_bytes)
    if start > len(raw):
        raise ApiError(400, "INVALID_RANGE", "start exceeds file length")
    clamped_end = min(end, len(raw))

    slice_bytes = raw[start:clamped_end]
    try:
        content = slice_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(
            400,
            "INVALID_RANGE",
            "Byte range splits a multi-byte UTF-8 character",
        ) from exc

    return SourceFileRangeResponse(
        id=source_file.id,
        file_path=source_file.file_path,
        start_byte=start,
        end_byte=clamped_end,
        content=content,
        bytes=len(slice_bytes),
    )


async def _require_ready_repository(
    *,
    session: AsyncSession,
    host: str,
    owner: str,
    name: str,
    settings: Settings,
    current_user: User | None,
) -> Repository:
    """Used by node-detail and source-file endpoints.

    Returns 404 when the repo doesn't exist or is not yet ready — the node or
    file being requested simply doesn't exist yet.
    """
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if repository.status is not RepositoryStatus.READY:
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    return repository
