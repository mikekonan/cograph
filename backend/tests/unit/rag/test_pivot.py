from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.app.models.enums import CodeNodeType
from backend.app.rag.pivot import GraphPivot


@dataclass(slots=True)
class _Related:
    id: object
    name: str
    node_type: CodeNodeType
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None


@dataclass(slots=True)
class _Detail:
    id: object
    name: str
    node_type: CodeNodeType
    language: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    callers: list[_Related]
    callees: list[_Related]
    parent: _Related | None


class _StubQueryService:
    def __init__(self, details_by_id):
        self.details_by_id = details_by_id
        self.calls: list[object] = []

    async def get_node_detail(self, *, session, repository_id, node_id):
        del session, repository_id
        self.calls.append(node_id)
        return self.details_by_id.get(node_id)


@pytest.mark.asyncio
async def test_expand_caps_unique_nodes_and_skips_orphans():
    a, b, missing, c = uuid4(), uuid4(), uuid4(), uuid4()
    helper = _Related(
        id=uuid4(),
        name="helper",
        node_type=CodeNodeType.FUNCTION,
        file_path="svc.py",
        start_line=10,
        end_line=12,
    )
    detail_a = _Detail(
        id=a,
        name="login",
        node_type=CodeNodeType.FUNCTION,
        language="python",
        file_path="svc.py",
        start_line=1,
        end_line=9,
        signature="def login(user_id: str) -> bool",
        callers=[],
        callees=[helper],
        parent=None,
    )
    detail_b = _Detail(
        id=b,
        name="audit",
        node_type=CodeNodeType.FUNCTION,
        language="python",
        file_path="svc.py",
        start_line=14,
        end_line=18,
        signature="def audit(user_id: str) -> None",
        callers=[helper],
        callees=[],
        parent=None,
    )
    stub = _StubQueryService({a: detail_a, b: detail_b, c: detail_b})
    pivot = GraphPivot(query_service=stub, max_nodes=3)

    result = await pivot.expand(
        session=AsyncMock(),
        repository_id=uuid4(),
        node_ids=[a, a, b, missing, c],
    )

    assert list(result) == [a, b]
    assert stub.calls == [a, b, missing]
    assert result[a].callees[0].name == "helper"
    assert result[b].callers[0].name == "helper"
