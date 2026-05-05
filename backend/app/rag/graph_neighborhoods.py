from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph.queries import GraphQueryService, GraphRelatedNode


@dataclass(slots=True, kw_only=True)
class GraphNeighborhood:
    """Neighborhood context for a single code node."""

    node_id: str
    name: str
    node_type: str
    file_path: str
    start_line: int
    end_line: int
    callers: list[dict[str, object]] = field(default_factory=list)
    callees: list[dict[str, object]] = field(default_factory=list)
    members: list[dict[str, object]] = field(default_factory=list)
    parent: dict[str, object] | None = None


@dataclass(slots=True, kw_only=True)
class GraphNeighborhoodBatch:
    """Batch result for multiple node neighborhoods."""

    neighborhoods: list[GraphNeighborhood] = field(default_factory=list)


class GraphNeighborhoodService:
    """Expand graph neighborhoods for agent API/MCP payloads."""

    def __init__(self, queries: GraphQueryService | None = None) -> None:
        self._queries = queries or GraphQueryService()

    async def expand_for_nodes(
        self,
        session: AsyncSession,
        *,
        repository_id: UUID,
        node_ids: list[UUID],
    ) -> GraphNeighborhoodBatch:
        """Fetch neighborhoods for a batch of code nodes."""
        neighborhoods: list[GraphNeighborhood] = []
        for node_id in node_ids:
            detail = await self._queries.get_node_detail(
                session=session,
                repository_id=repository_id,
                node_id=node_id,
            )
            if detail is None:
                continue
            neighborhoods.append(
                GraphNeighborhood(
                    node_id=str(detail.id),
                    name=detail.name,
                    node_type=detail.node_type.value,
                    file_path=detail.file_path,
                    start_line=detail.start_line,
                    end_line=detail.end_line,
                    callers=[_related_to_dict(r) for r in detail.callers],
                    callees=[_related_to_dict(r) for r in detail.callees],
                    members=[_related_to_dict(r) for r in detail.members],
                    parent=_related_to_dict(detail.parent) if detail.parent else None,
                )
            )
        return GraphNeighborhoodBatch(neighborhoods=neighborhoods)

    async def expand_for_search_results(
        self,
        session: AsyncSession,
        *,
        repository_id: UUID,
        code_result_node_ids: list[UUID],
    ) -> dict[str, GraphNeighborhood]:
        """Return a mapping from node_id string to neighborhood.

        This is designed to be merged into blended search response metadata.
        """
        batch = await self.expand_for_nodes(
            session=session,
            repository_id=repository_id,
            node_ids=code_result_node_ids,
        )
        return {n.node_id: n for n in batch.neighborhoods}


def _related_to_dict(node: GraphRelatedNode | None) -> dict[str, object] | None:
    if node is None:
        return None
    return {
        "id": str(node.id),
        "name": node.name,
        "node_type": node.node_type.value,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "signature": node.signature,
    }


__all__ = [
    "GraphNeighborhood",
    "GraphNeighborhoodBatch",
    "GraphNeighborhoodService",
]
