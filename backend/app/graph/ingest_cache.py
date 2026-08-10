from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType


@dataclass
class GraphIngestCache:
    """In-memory mirror of CodeNode rows for one ingest run.

    Built once at the top of an ingest from a single repo-wide SELECT,
    then mutated in place by each `persist_graph` call so the next file
    sees the freshest state without hitting the DB.

    Mutation contract: persist_graph is the *only* writer. After it
    returns, the cache must reflect every insert/update/delete it made.
    """

    node_by_id: dict[UUID, CodeNode] = field(default_factory=dict)
    nodes_by_qn: dict[str, CodeNode] = field(default_factory=dict)
    module_nodes_by_file_path: dict[str, CodeNode] = field(default_factory=dict)

    @classmethod
    def from_nodes(cls, nodes: list[CodeNode]) -> "GraphIngestCache":
        cache = cls()
        for node in nodes:
            cache.add(node)
        return cache

    def add(self, node: CodeNode) -> None:
        self.node_by_id[node.id] = node
        self.nodes_by_qn[node.qualified_name] = node
        if node.node_type is CodeNodeType.MODULE:
            self.module_nodes_by_file_path[node.file_path] = node

    def remove(self, node: CodeNode) -> None:
        self.node_by_id.pop(node.id, None)
        if self.nodes_by_qn.get(node.qualified_name) is node:
            self.nodes_by_qn.pop(node.qualified_name, None)
        if self.module_nodes_by_file_path.get(node.file_path) is node:
            self.module_nodes_by_file_path.pop(node.file_path, None)

    def rename(self, node: CodeNode, old_qualified_name: str) -> None:
        if old_qualified_name == node.qualified_name:
            return
        if self.nodes_by_qn.get(old_qualified_name) is node:
            self.nodes_by_qn.pop(old_qualified_name, None)
        self.nodes_by_qn[node.qualified_name] = node

    def repository_nodes(self) -> list[CodeNode]:
        return list(self.node_by_id.values())
