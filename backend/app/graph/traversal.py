from __future__ import annotations

import uuid
from collections import defaultdict
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph.extractor import GraphEdgeType
from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository


class TraversalDirection(StrEnum):
    CALLERS = "callers"
    CALLEES = "callees"
    BOTH = "both"


class TraversalNode(BaseModel):
    id: UUID
    name: str
    node_type: CodeNodeType
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    distance: int


class TraversalEdge(BaseModel):
    source: UUID
    target: UUID
    type: str
    distance: int


class TraversalResponse(BaseModel):
    root: TraversalNode
    direction: TraversalDirection
    depth: int
    nodes: list[TraversalNode]
    edges: list[TraversalEdge]


class GraphTraversalService:
    async def traverse(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        node_id: UUID,
        depth: int = 1,
        direction: TraversalDirection = TraversalDirection.BOTH,
    ) -> TraversalResponse | None:
        root = await session.scalar(
            select(CodeNode).where(
                CodeNode.repository_id == repository_id,
                CodeNode.id == node_id,
            )
        )
        if root is None:
            return None

        repository = await session.get(Repository, repository_id)
        use_v2 = repository is not None and (repository.graph_storage_version or 1) >= 2

        visited_distance: dict[UUID, int] = {node_id: 0}
        edge_distance: dict[tuple[UUID, UUID], int] = {}
        frontier: list[UUID] = [node_id]

        for hop in range(1, depth + 1):
            if not frontier:
                break

            neighbour_map = await self._load_neighbours(
                session=session,
                repository_id=repository_id,
                frontier=frontier,
                direction=direction,
                use_v2=use_v2,
            )

            next_frontier: list[UUID] = []
            for current_id in frontier:
                for related_id, source_id, target_id in neighbour_map.get(current_id, []):
                    edge_distance.setdefault((source_id, target_id), hop)
                    if related_id in visited_distance:
                        continue
                    visited_distance[related_id] = hop
                    next_frontier.append(related_id)

            frontier = next_frontier

        nodes = list(
            (
                await session.scalars(
                    select(CodeNode).where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.id.in_(list(visited_distance)),
                    )
                )
            ).all()
        )
        node_by_id = {node.id: node for node in nodes}
        root_node = node_by_id.get(node_id)
        if root_node is None:
            return None

        visible_ids = set(node_by_id)
        related_nodes = [
            TraversalNode(
                id=node.id,
                name=node.name,
                node_type=node.node_type,
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
                signature=node.signature,
                distance=visited_distance[node.id],
            )
            for node in sorted(
                (item for item in nodes if item.id != node_id),
                key=lambda item: (
                    visited_distance[item.id],
                    item.file_path,
                    item.start_line,
                    item.name,
                ),
            )
        ]
        edges = [
            TraversalEdge(
                source=source_id,
                target=target_id,
                type=GraphEdgeType.CALLS.value,
                distance=hop,
            )
            for (source_id, target_id), hop in sorted(
                edge_distance.items(),
                key=lambda item: (item[1], str(item[0][0]), str(item[0][1])),
            )
            if source_id in visible_ids and target_id in visible_ids
        ]

        return TraversalResponse(
            root=TraversalNode(
                id=root_node.id,
                name=root_node.name,
                node_type=root_node.node_type,
                file_path=root_node.file_path,
                start_line=root_node.start_line,
                end_line=root_node.end_line,
                signature=root_node.signature,
                distance=0,
            ),
            direction=direction,
            depth=depth,
            nodes=related_nodes,
            edges=edges,
        )

    async def _load_neighbours(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        frontier: list[UUID],
        direction: TraversalDirection,
        use_v2: bool,
    ) -> dict[UUID, list[tuple[UUID, UUID, UUID]]]:
        if use_v2:
            return await self._load_neighbours_v2(
                session=session,
                repository_id=repository_id,
                frontier=frontier,
                direction=direction,
            )
        return await self._load_neighbours_legacy(
            session=session,
            repository_id=repository_id,
            frontier=frontier,
            direction=direction,
        )

    async def _load_neighbours_v2(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        frontier: list[UUID],
        direction: TraversalDirection,
    ) -> dict[UUID, list[tuple[UUID, UUID, UUID]]]:
        neighbours: dict[UUID, list[tuple[UUID, UUID, UUID]]] = defaultdict(list)

        if direction in {TraversalDirection.CALLEES, TraversalDirection.BOTH}:
            rows = (
                await session.execute(
                    select(CodeEdge.source_node_id, CodeEdge.target_node_id)
                    .where(
                        CodeEdge.repository_id == repository_id,
                        CodeEdge.edge_type == GraphEdgeType.CALLS.value,
                        CodeEdge.source_node_id.in_(frontier),
                        CodeEdge.target_node_id.is_not(None),
                    )
                    .order_by(CodeEdge.source_node_id, CodeEdge.target_node_id)
                )
            ).all()
            for source_id, target_id in rows:
                if target_id is None:
                    continue
                neighbours[source_id].append((target_id, source_id, target_id))

        if direction in {TraversalDirection.CALLERS, TraversalDirection.BOTH}:
            rows = (
                await session.execute(
                    select(CodeEdge.target_node_id, CodeEdge.source_node_id)
                    .where(
                        CodeEdge.repository_id == repository_id,
                        CodeEdge.edge_type == GraphEdgeType.CALLS.value,
                        CodeEdge.target_node_id.in_(frontier),
                        CodeEdge.source_node_id.is_not(None),
                    )
                    .order_by(CodeEdge.target_node_id, CodeEdge.source_node_id)
                )
            ).all()
            for target_id, source_id in rows:
                if target_id is None or source_id is None:
                    continue
                neighbours[target_id].append((source_id, source_id, target_id))

        return neighbours

    async def _load_neighbours_legacy(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        frontier: list[UUID],
        direction: TraversalDirection,
    ) -> dict[UUID, list[tuple[UUID, UUID, UUID]]]:
        nodes = list(
            (
                await session.scalars(
                    select(CodeNode).where(
                        CodeNode.repository_id == repository_id,
                        CodeNode.id.in_(frontier),
                    )
                )
            ).all()
        )

        neighbours: dict[UUID, list[tuple[UUID, UUID, UUID]]] = defaultdict(list)
        for node in nodes:
            if direction in {TraversalDirection.CALLEES, TraversalDirection.BOTH}:
                for raw_id in node.callees:
                    target_id = self._parse_uuid(raw_id)
                    if target_id is None:
                        continue
                    neighbours[node.id].append((target_id, node.id, target_id))

            if direction in {TraversalDirection.CALLERS, TraversalDirection.BOTH}:
                for raw_id in node.callers:
                    source_id = self._parse_uuid(raw_id)
                    if source_id is None:
                        continue
                    neighbours[node.id].append((source_id, source_id, node.id))

        return neighbours

    def _parse_uuid(self, value: str) -> UUID | None:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None
