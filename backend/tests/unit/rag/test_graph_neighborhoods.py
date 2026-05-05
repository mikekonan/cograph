from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.models.enums import CodeNodeType
from backend.app.rag.graph_neighborhoods import (
    GraphNeighborhoodBatch,
    GraphNeighborhoodService,
    _related_to_dict,
)


class FakeGraphRelatedNode:
    def __init__(self, id, name, node_type, file_path, start_line, end_line, signature=None):
        self.id = id
        self.name = name
        self.node_type = node_type if isinstance(node_type, CodeNodeType) else CodeNodeType(node_type)
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.signature = signature


class FakeGraphNodeDetail:
    def __init__(self, id, name, node_type, file_path, start_line, end_line, callers=None, callees=None, members=None, parent=None):
        self.id = id
        self.name = name
        self.node_type = node_type if isinstance(node_type, CodeNodeType) else CodeNodeType(node_type)
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.callers = callers or []
        self.callees = callees or []
        self.members = members or []
        self.parent = parent


class FakeGraphQueries:
    def __init__(self, details: dict | None = None):
        self.details = details or {}

    async def get_node_detail(self, session, repository_id, node_id):
        return self.details.get(node_id)


def test_related_to_dict():
    node = FakeGraphRelatedNode(
        id=uuid4(),
        name="main.Run",
        node_type="function",
        file_path="main.go",
        start_line=10,
        end_line=20,
        signature="func Run()",
    )
    d = _related_to_dict(node)
    assert d is not None
    assert d["name"] == "main.Run"
    assert d["file_path"] == "main.go"
    assert d["start_line"] == 10


def test_related_to_dict_none():
    assert _related_to_dict(None) is None


@pytest.mark.anyio
async def test_expand_for_nodes():
    node_id = uuid4()
    detail = FakeGraphNodeDetail(
        id=node_id,
        name="main.Run",
        node_type="function",
        file_path="main.go",
        start_line=10,
        end_line=20,
        callers=[FakeGraphRelatedNode(uuid4(), "main.Init", CodeNodeType.FUNCTION, "main.go", 1, 5)],
    )
    queries = FakeGraphQueries(details={node_id: detail})
    service = GraphNeighborhoodService(queries=queries)

    batch = await service.expand_for_nodes(
        session=None,  # type: ignore[arg-type]
        repository_id=node_id,
        node_ids=[node_id],
    )
    assert isinstance(batch, GraphNeighborhoodBatch)
    assert len(batch.neighborhoods) == 1
    nb = batch.neighborhoods[0]
    assert nb.name == "main.Run"
    assert len(nb.callers) == 1
    assert nb.callers[0]["name"] == "main.Init"


@pytest.mark.anyio
async def test_expand_for_nodes_missing_node():
    queries = FakeGraphQueries(details={})
    service = GraphNeighborhoodService(queries=queries)

    batch = await service.expand_for_nodes(
        session=None,  # type: ignore[arg-type]
        repository_id=uuid4(),
        node_ids=[uuid4()],
    )
    assert batch.neighborhoods == []


@pytest.mark.anyio
async def test_expand_for_search_results():
    node_id = uuid4()
    detail = FakeGraphNodeDetail(
        id=node_id,
        name="main.Run",
        node_type="function",
        file_path="main.go",
        start_line=10,
        end_line=20,
        callees=[FakeGraphRelatedNode(uuid4(), "db.Connect", CodeNodeType.FUNCTION, "db.go", 30, 40)],
    )
    queries = FakeGraphQueries(details={node_id: detail})
    service = GraphNeighborhoodService(queries=queries)

    result = await service.expand_for_search_results(
        session=None,  # type: ignore[arg-type]
        repository_id=node_id,
        code_result_node_ids=[node_id],
    )
    assert str(node_id) in result
    assert result[str(node_id)].name == "main.Run"
    assert len(result[str(node_id)].callees) == 1
