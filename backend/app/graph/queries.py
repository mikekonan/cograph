from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.sql.selectable import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from backend.app.graph.extractor import GraphEdgeType
from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile


class GraphView(StrEnum):
    ARCHITECTURE = "architecture"
    SYMBOLS = "symbols"


@dataclass(slots=True, kw_only=True)
class GraphNodeSummary:
    id: UUID
    name: str
    node_type: CodeNodeType
    language: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    parent_id: UUID | None
    parent_name: str | None


@dataclass(slots=True, kw_only=True)
class GraphEdgeSummary:
    source: UUID
    target: UUID
    edge_type: GraphEdgeType


@dataclass(slots=True, kw_only=True)
class GraphListStats:
    total_nodes: int
    matched_nodes: int
    returned_nodes: int
    languages: dict[str, int]


@dataclass(slots=True, kw_only=True)
class GraphListResult:
    nodes: list[GraphNodeSummary]
    edges: list[GraphEdgeSummary]
    stats: GraphListStats


@dataclass(slots=True, kw_only=True)
class GraphRelatedNode:
    id: UUID
    name: str
    node_type: CodeNodeType
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None


@dataclass(slots=True, kw_only=True)
class GraphNodeDetail:
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
    callers: list[GraphRelatedNode]
    callees: list[GraphRelatedNode]
    members: list[GraphRelatedNode]
    parent: GraphRelatedNode | None


@dataclass(slots=True, kw_only=True)
class GraphQueryFilters:
    view: GraphView = GraphView.ARCHITECTURE
    node_type: CodeNodeType | None = None
    language: str | None = None
    module: str | None = None
    search: str | None = None
    limit: int = 200


def build_graph_edges(
    *,
    selected_nodes: list[CodeNode],
    node_by_id: dict[UUID, CodeNode],
    node_by_qualified_name: dict[str, CodeNode],
) -> list[GraphEdgeSummary]:
    edges: dict[tuple[UUID, UUID, GraphEdgeType], GraphEdgeSummary] = {}
    for node in selected_nodes:
        if node.parent_id is not None and node.parent_id in node_by_id:
            edges[(node.parent_id, node.id, GraphEdgeType.DECLARES)] = GraphEdgeSummary(
                source=node.parent_id,
                target=node.id,
                edge_type=GraphEdgeType.DECLARES,
            )

        for callee_id in node.callees:
            target_id = UUID(callee_id)
            if target_id not in node_by_id:
                continue
            edges[(node.id, target_id, GraphEdgeType.CALLS)] = GraphEdgeSummary(
                source=node.id,
                target=target_id,
                edge_type=GraphEdgeType.CALLS,
            )

        for inherited_name in _metadata_list(node.node_metadata, "inherits"):
            target_node = _resolve_qualified_or_bare_name(
                inherited_name,
                node_by_qualified_name,
            )
            if target_node is None:
                continue
            edges[(node.id, target_node.id, GraphEdgeType.INHERITS)] = GraphEdgeSummary(
                source=node.id,
                target=target_node.id,
                edge_type=GraphEdgeType.INHERITS,
            )

        for imported_name in _metadata_list(node.node_metadata, "imports"):
            target_node = _resolve_qualified_or_bare_name(
                imported_name,
                node_by_qualified_name,
            )
            if target_node is None:
                continue
            edges[(node.id, target_node.id, GraphEdgeType.IMPORTS)] = GraphEdgeSummary(
                source=node.id,
                target=target_node.id,
                edge_type=GraphEdgeType.IMPORTS,
            )

    return list(edges.values())


class GraphQueryService:
    async def list_graph(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        filters: GraphQueryFilters | None = None,
    ) -> GraphListResult:
        resolved_filters = filters or GraphQueryFilters()

        total_nodes = await session.scalar(
            select(func.count())
            .select_from(CodeNode)
            .where(CodeNode.repository_id == repository_id)
        )
        language_rows = await session.execute(
            select(CodeNode.language, func.count())
            .where(CodeNode.repository_id == repository_id)
            .group_by(CodeNode.language)
            .order_by(CodeNode.language)
        )
        languages = {language: count for language, count in language_rows.all()}

        base_query = (
            select(CodeNode)
            .options(
                load_only(
                    CodeNode.id,
                    CodeNode.name,
                    CodeNode.node_type,
                    CodeNode.language,
                    CodeNode.file_path,
                    CodeNode.start_line,
                    CodeNode.end_line,
                    CodeNode.signature,
                    CodeNode.parent_id,
                    CodeNode.qualified_name,
                    CodeNode.callees,
                    CodeNode.node_metadata,
                    CodeNode.role,
                )
            )
            .where(CodeNode.repository_id == repository_id)
        )
        filtered_query = self._apply_filters(
            base_query,
            resolved_filters,
        )
        matched_nodes = await session.scalar(
            select(func.count()).select_from(filtered_query.subquery())
        )
        selected_nodes = list(
            (
                await session.scalars(
                    filtered_query.order_by(
                        CodeNode.file_path, CodeNode.start_line, CodeNode.name
                    ).limit(resolved_filters.limit)
                )
            ).all()
        )

        node_by_id = {node.id: node for node in selected_nodes}
        node_by_qualified_name = {node.qualified_name: node for node in selected_nodes}

        summaries = [
            GraphNodeSummary(
                id=node.id,
                name=node.name,
                node_type=node.node_type,
                language=node.language,
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
                signature=node.signature,
                parent_id=node.parent_id,
                parent_name=node_by_id[node.parent_id].name
                if node.parent_id in node_by_id
                else None,
            )
            for node in selected_nodes
        ]
        edges = self._build_edges(
            selected_nodes=selected_nodes,
            node_by_id=node_by_id,
            node_by_qualified_name=node_by_qualified_name,
        )

        return GraphListResult(
            nodes=summaries,
            edges=edges,
            stats=GraphListStats(
                total_nodes=total_nodes or 0,
                matched_nodes=matched_nodes or 0,
                returned_nodes=len(selected_nodes),
                languages=languages,
            ),
        )

    async def resolve_node_id_by_qualified_name(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        qualified_name: str,
    ) -> UUID | None:
        """Look up the current UUID of a code node by its `qualified_name`.

        Used by the wiki citation fallback path: persisted markdown carries
        a frozen `?node=<uuid>` href; if that UUID 404s after a re-index
        moved/renamed the symbol, the FE retries against this resolver
        with the citation's `qualified_name` to find the new UUID.
        """
        return await session.scalar(
            select(CodeNode.id).where(
                CodeNode.repository_id == repository_id,
                CodeNode.qualified_name == qualified_name,
            )
        )

    async def existing_node_ids(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        node_ids: list[UUID],
    ) -> set[UUID]:
        """Return the subset of `node_ids` that still exist in `code_nodes`.

        Used by the wiki page metadata panel to compute a stale-citation
        chip — pages persist UUID-bearing hrefs, and re-indexes can drop
        nodes when their underlying symbol was renamed/removed/moved.
        """
        if not node_ids:
            return set()
        rows = await session.scalars(
            select(CodeNode.id).where(
                CodeNode.repository_id == repository_id,
                CodeNode.id.in_(node_ids),
            )
        )
        return set(rows.all())

    async def get_node_detail(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        node_id: UUID,
    ) -> GraphNodeDetail | None:
        node = await session.scalar(
            select(CodeNode).where(
                CodeNode.repository_id == repository_id,
                CodeNode.id == node_id,
            )
        )
        if node is None:
            return None

        # Per-repo cutover flag. When graph_storage_version >= 2 the repo has
        # been flipped onto source_files byte slices + code_edges; legacy
        # columns (content, callers, callees) are still kept in sync during
        # co-existence but will be dropped by 0008_finalize. Reading through
        # the gate now lets operators verify v2 before the drop lands.
        repository = await session.get(Repository, repository_id)
        use_v2 = repository is not None and (repository.graph_storage_version or 1) >= 2

        parent = None
        if node.parent_id is not None:
            parent_node = await session.get(CodeNode, node.parent_id)
            if parent_node is not None:
                parent = _to_related_node(parent_node)

        if use_v2:
            content = await _v2_node_content(session=session, node=node)
            callers = await self._v2_related(
                session=session,
                repository_id=repository_id,
                node_id=node.id,
                direction="inbound",
            )
            callees = await self._v2_related(
                session=session,
                repository_id=repository_id,
                node_id=node.id,
                direction="outbound",
            )
        else:
            content = node.content
            callers = await self._related_nodes_from_ids(
                session=session,
                repository_id=repository_id,
                ids=node.callers,
            )
            callees = await self._related_nodes_from_ids(
                session=session,
                repository_id=repository_id,
                ids=node.callees,
            )

        members = [
            _to_related_node(member)
            for member in (
                await session.scalars(
                    select(CodeNode)
                    .where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.parent_id == node.id,
                    )
                    .order_by(CodeNode.start_line, CodeNode.name)
                )
            ).all()
        ]

        return GraphNodeDetail(
            id=node.id,
            name=node.name,
            node_type=node.node_type,
            language=node.language,
            file_path=node.file_path,
            start_line=node.start_line,
            end_line=node.end_line,
            content=content,
            signature=node.signature,
            doc_comment=node.doc_comment,
            metadata=node.node_metadata,
            callers=callers,
            callees=callees,
            members=members,
            parent=parent,
        )

    async def _v2_related(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        node_id: UUID,
        direction: str,
    ) -> list[GraphRelatedNode]:
        # direction == "outbound" → callees: edges where we are the source.
        # direction == "inbound"  → callers: edges where we are the target.
        if direction == "outbound":
            peer_id_query: Select[tuple[UUID | None]] = select(
                CodeEdge.target_node_id
            ).where(
                CodeEdge.repository_id == repository_id,
                CodeEdge.source_node_id == node_id,
                CodeEdge.edge_type == GraphEdgeType.CALLS.value,
                CodeEdge.target_node_id.is_not(None),
            )
        else:
            peer_id_query = select(CodeEdge.source_node_id).where(  # type: ignore[assignment]
                CodeEdge.repository_id == repository_id,
                CodeEdge.target_node_id == node_id,
                CodeEdge.edge_type == GraphEdgeType.CALLS.value,
            )
        peer_ids = [row for row in (await session.scalars(peer_id_query)).all() if row]
        if not peer_ids:
            return []
        nodes = list(
            (
                await session.scalars(
                    select(CodeNode)
                    .where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.id.in_(peer_ids),
                    )
                    .order_by(CodeNode.file_path, CodeNode.start_line, CodeNode.name)
                )
            ).all()
        )
        return [_to_related_node(node) for node in nodes]

    async def _related_nodes_from_ids(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        ids: list[str],
    ) -> list[GraphRelatedNode]:
        related_ids = [UUID(value) for value in ids]
        if not related_ids:
            return []

        nodes = list(
            (
                await session.scalars(
                    select(CodeNode)
                    .where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.id.in_(related_ids),
                    )
                    .order_by(CodeNode.file_path, CodeNode.start_line, CodeNode.name)
                )
            ).all()
        )
        node_by_id = {node.id: node for node in nodes}
        return [
            _to_related_node(node_by_id[related_id])
            for related_id in related_ids
            if related_id in node_by_id
        ]

    def _apply_filters(self, query, filters: GraphQueryFilters):
        if filters.view is GraphView.ARCHITECTURE:
            query = query.where(
                CodeNode.node_type.in_(
                    [
                        CodeNodeType.MODULE,
                        CodeNodeType.CLASS,
                        CodeNodeType.STRUCT,
                        CodeNodeType.INTERFACE,
                    ]
                )
            )

        if filters.node_type is not None:
            query = query.where(CodeNode.node_type == filters.node_type)

        if filters.language:
            query = query.where(CodeNode.language == filters.language)

        if filters.module:
            query = query.where(CodeNode.file_path.startswith(filters.module))

        if filters.search:
            search_term = f"%{filters.search.lower()}%"
            query = query.where(func.lower(CodeNode.name).like(search_term))

        return query

    def _build_edges(
        self,
        *,
        selected_nodes: list[CodeNode],
        node_by_id: dict[UUID, CodeNode],
        node_by_qualified_name: dict[str, CodeNode],
    ) -> list[GraphEdgeSummary]:
        return build_graph_edges(
            selected_nodes=selected_nodes,
            node_by_id=node_by_id,
            node_by_qualified_name=node_by_qualified_name,
        )


async def _v2_node_content(*, session: AsyncSession, node: CodeNode) -> str:
    """Slice the node's body out of its SourceFile.raw_bytes.

    Falls back to the legacy `code_nodes.content` column when the node is not
    wired to a source_file yet (e.g. mid-migration). After 0008_finalize the
    column will be gone and this helper becomes authoritative.
    """
    if node.source_file_id is None or node.start_byte is None or node.end_byte is None:
        return node.content
    source_file = await session.get(SourceFile, node.source_file_id)
    if source_file is None or source_file.raw_bytes is None:
        return node.content
    start = max(0, node.start_byte)
    end = min(len(source_file.raw_bytes), node.end_byte)
    if end <= start:
        return ""
    try:
        return source_file.raw_bytes[start:end].decode("utf-8")
    except UnicodeDecodeError:
        # Byte range from a parser should always align with codepoints, but
        # fall back gracefully if the source file has a mid-codepoint slice.
        return node.content


def _to_related_node(node: CodeNode) -> GraphRelatedNode:
    return GraphRelatedNode(
        id=node.id,
        name=node.name,
        node_type=node.node_type,
        file_path=node.file_path,
        start_line=node.start_line,
        end_line=node.end_line,
        signature=node.signature,
    )


def _metadata_list(metadata: dict[str, object], key: str) -> list[str]:
    values = metadata.get(key)
    return list(values) if isinstance(values, list) else []


def _resolve_qualified_or_bare_name(
    target_name: str,
    node_by_qualified_name: dict[str, CodeNode],
) -> CodeNode | None:
    if target_name in node_by_qualified_name:
        return node_by_qualified_name[target_name]

    matches = [
        node
        for qualified_name, node in node_by_qualified_name.items()
        if qualified_name.endswith(f".{target_name}") or node.name == target_name
    ]
    if len(matches) == 1:
        return matches[0]
    return None
