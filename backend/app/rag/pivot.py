"""Graph pivot helper for composite retrieval responses (Phase 7e).

``GraphPivot`` intentionally stays small and explicit:

* Expands only the first ``max_nodes`` unique code hits to avoid unbounded
  per-result graph fan-out (the phase plan caps this at 20).
* Reuses ``GraphQueryService.get_node_detail()`` so retrieval and graph pages
  read the same callers/callees/parent relationships.
* Skips orphan / missing nodes instead of failing the whole request.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph.queries import GraphQueryService
from backend.app.models.enums import CodeNodeType


@dataclass(slots=True, kw_only=True)
class PivotRelatedNode:
    id: UUID
    name: str
    node_type: CodeNodeType
    file_path: str
    start_line: int | None
    end_line: int | None
    signature: str | None


@dataclass(slots=True, kw_only=True)
class PivotNode:
    id: UUID
    name: str
    node_type: CodeNodeType
    language: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    callers: list[PivotRelatedNode]
    callees: list[PivotRelatedNode]
    parent: PivotRelatedNode | None


class GraphPivot:
    def __init__(
        self,
        *,
        query_service: GraphQueryService | None = None,
        max_nodes: int = 20,
    ) -> None:
        self.query_service = query_service or GraphQueryService()
        self.max_nodes = int(max_nodes)

    async def expand(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        node_ids: list[UUID],
    ) -> dict[UUID, PivotNode]:
        unique_ids: list[UUID] = []
        seen: set[UUID] = set()
        for node_id in node_ids:
            if node_id in seen:
                continue
            seen.add(node_id)
            unique_ids.append(node_id)
            if len(unique_ids) >= self.max_nodes:
                break

        expanded: dict[UUID, PivotNode] = {}
        for node_id in unique_ids:
            detail = await self.query_service.get_node_detail(
                session=session,
                repository_id=repository_id,
                node_id=node_id,
            )
            if detail is None:
                continue
            expanded[node_id] = PivotNode(
                id=detail.id,
                name=detail.name,
                node_type=detail.node_type,
                language=detail.language,
                file_path=detail.file_path,
                start_line=detail.start_line,
                end_line=detail.end_line,
                signature=detail.signature,
                callers=[_to_related_node(node) for node in detail.callers],
                callees=[_to_related_node(node) for node in detail.callees],
                parent=_to_related_node(detail.parent) if detail.parent is not None else None,
            )
        return expanded


def _to_related_node(node) -> PivotRelatedNode:
    return PivotRelatedNode(
        id=node.id,
        name=node.name,
        node_type=node.node_type,
        file_path=node.file_path,
        start_line=node.start_line,
        end_line=node.end_line,
        signature=node.signature,
    )
