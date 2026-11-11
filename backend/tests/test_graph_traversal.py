from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.graph.extractor import GraphEdgeType
from backend.app.graph.traversal import GraphTraversalService, TraversalDirection
from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository


@pytest.mark.asyncio
async def test_graph_traversal_walks_legacy_callers_and_callees(db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    root_id = uuid4()
    caller_id = uuid4()
    callee_id = uuid4()
    caller2_id = uuid4()
    callee2_id = uuid4()

    caller2 = CodeNode(
        id=caller2_id,
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.caller2",
        symbol_key="svc.caller2",
        node_type=CodeNodeType.FUNCTION,
        name="caller2",
        language="python",
        start_line=1,
        end_line=3,
        start_byte=None,
        end_byte=None,
        content="def caller2(): pass",
        signature="def caller2()",
        doc_comment=None,
        role=None,
        parent_id=None,
        callers=[],
        callees=[str(caller_id)],
        node_metadata={},
        content_hash="caller2",
    )
    caller = CodeNode(
        id=caller_id,
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.caller",
        symbol_key="svc.caller",
        node_type=CodeNodeType.FUNCTION,
        name="caller",
        language="python",
        start_line=5,
        end_line=7,
        start_byte=None,
        end_byte=None,
        content="def caller(): pass",
        signature="def caller()",
        doc_comment=None,
        role=None,
        parent_id=None,
        callers=[str(caller2_id)],
        callees=[str(root_id)],
        node_metadata={},
        content_hash="caller",
    )
    root = CodeNode(
        id=root_id,
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.root",
        symbol_key="svc.root",
        node_type=CodeNodeType.FUNCTION,
        name="root",
        language="python",
        start_line=9,
        end_line=11,
        start_byte=None,
        end_byte=None,
        content="def root(): pass",
        signature="def root()",
        doc_comment=None,
        role=None,
        parent_id=None,
        callers=[str(caller_id)],
        callees=[str(callee_id)],
        node_metadata={},
        content_hash="root",
    )
    callee = CodeNode(
        id=callee_id,
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.callee",
        symbol_key="svc.callee",
        node_type=CodeNodeType.FUNCTION,
        name="callee",
        language="python",
        start_line=13,
        end_line=15,
        start_byte=None,
        end_byte=None,
        content="def callee(): pass",
        signature="def callee()",
        doc_comment=None,
        role=None,
        parent_id=None,
        callers=[str(root_id)],
        callees=[str(callee2_id)],
        node_metadata={},
        content_hash="callee",
    )
    callee2 = CodeNode(
        id=callee2_id,
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.callee2",
        symbol_key="svc.callee2",
        node_type=CodeNodeType.FUNCTION,
        name="callee2",
        language="python",
        start_line=17,
        end_line=19,
        start_byte=None,
        end_byte=None,
        content="def callee2(): pass",
        signature="def callee2()",
        doc_comment=None,
        role=None,
        parent_id=None,
        callers=[str(callee_id)],
        callees=[],
        node_metadata={},
        content_hash="callee2",
    )
    db_session.add_all([caller2, caller, root, callee, callee2])
    await db_session.commit()

    result = await GraphTraversalService().traverse(
        session=db_session,
        repository_id=repository.id,
        node_id=root.id,
        depth=2,
        direction=TraversalDirection.BOTH,
    )

    assert result is not None
    assert result.root.id == root.id
    assert result.truncated is False
    assert [(node.name, node.distance) for node in result.nodes] == [
        ("caller", 1),
        ("callee", 1),
        ("caller2", 2),
        ("callee2", 2),
    ]
    assert {(str(edge.source), str(edge.target), edge.distance) for edge in result.edges} == {
        (str(caller.id), str(root.id), 1),
        (str(root.id), str(callee.id), 1),
        (str(caller2.id), str(caller.id), 2),
        (str(callee.id), str(callee2.id), 2),
    }

    # Same graph with max_nodes=2: only the depth-1 ring fits, the hop-2
    # nodes are dropped and the response says so. This is the cap
    # `cograph_related` relies on to never dump an unbounded neighbourhood
    # into an agent's context.
    capped = await GraphTraversalService().traverse(
        session=db_session,
        repository_id=repository.id,
        node_id=root.id,
        depth=2,
        direction=TraversalDirection.BOTH,
        max_nodes=2,
    )

    assert capped is not None
    assert capped.truncated is True
    assert [(node.name, node.distance) for node in capped.nodes] == [
        ("caller", 1),
        ("callee", 1),
    ]
    # Edges are filtered to visible nodes, so nothing may reference the
    # dropped hop-2 nodes.
    assert {(str(edge.source), str(edge.target)) for edge in capped.edges} == {
        (str(caller.id), str(root.id)),
        (str(root.id), str(callee.id)),
    }


@pytest.mark.asyncio
async def test_graph_traversal_uses_code_edges_for_v2_repositories(db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
        graph_storage_version=2,
    )
    db_session.add(repository)
    await db_session.flush()

    root = CodeNode(
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.root",
        symbol_key="svc.root",
        node_type=CodeNodeType.FUNCTION,
        name="root",
        language="python",
        start_line=1,
        end_line=3,
        start_byte=None,
        end_byte=None,
        content="def root(): pass",
        signature="def root()",
        doc_comment=None,
        role=None,
        parent_id=None,
        callers=[],
        callees=[],
        node_metadata={},
        content_hash="root",
    )
    callee = CodeNode(
        repository_id=repository.id,
        source_file_id=None,
        file_path="svc.py",
        qualified_name="svc.callee",
        symbol_key="svc.callee",
        node_type=CodeNodeType.FUNCTION,
        name="callee",
        language="python",
        start_line=5,
        end_line=7,
        start_byte=None,
        end_byte=None,
        content="def callee(): pass",
        signature="def callee()",
        doc_comment=None,
        role=None,
        parent_id=None,
        callers=[],
        callees=[],
        node_metadata={},
        content_hash="callee",
    )
    db_session.add_all([root, callee])
    await db_session.flush()
    db_session.add(
        CodeEdge(
            repository_id=repository.id,
            source_node_id=root.id,
            target_node_id=callee.id,
            target_qualified_name=callee.qualified_name,
            edge_type=GraphEdgeType.CALLS.value,
        )
    )
    await db_session.commit()

    result = await GraphTraversalService().traverse(
        session=db_session,
        repository_id=repository.id,
        node_id=root.id,
        depth=1,
        direction=TraversalDirection.CALLEES,
    )

    assert result is not None
    assert [(node.name, node.distance) for node in result.nodes] == [("callee", 1)]
    assert [(str(edge.source), str(edge.target), edge.type) for edge in result.edges] == [
        (str(root.id), str(callee.id), GraphEdgeType.CALLS.value)
    ]


@pytest.mark.asyncio
async def test_graph_traversal_cap_keeps_product_callers_first(db_session):
    """A hub's test and generated callers sort first by id here; the cap
    must still keep the product caller."""
    repository = Repository(
        host="example.com",
        git_url="git@example.com:acme/warehouse.git",
        name="warehouse",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
        graph_storage_version=2,
    )
    db_session.add(repository)
    await db_session.flush()

    def node(name: str, path: str, metadata: dict | None = None) -> CodeNode:
        return CodeNode(
            repository_id=repository.id,
            file_path=path,
            qualified_name=f"{path}.{name}",
            symbol_key=f"{path}.{name}",
            node_type=CodeNodeType.FUNCTION,
            name=name,
            language="python",
            start_line=1,
            end_line=2,
            content=f"def {name}(): pass",
            callers=[],
            callees=[],
            node_metadata=metadata or {},
            content_hash=name,
        )

    root = node("pack", "parcels.py")
    test_caller = node("test_pack", "tests/test_parcels.py")
    generated_caller = node("pack_stub", "client.py", {"generated": "openapi"})
    product_caller = node("ship", "route.py")
    # Flushed one by one so ids grow in this order: the product caller last.
    for item in (root, test_caller, generated_caller, product_caller):
        db_session.add(item)
        await db_session.flush()
    for caller in (test_caller, generated_caller, product_caller):
        db_session.add(
            CodeEdge(
                repository_id=repository.id,
                source_node_id=caller.id,
                target_node_id=root.id,
                target_qualified_name=root.qualified_name,
                edge_type=GraphEdgeType.CALLS.value,
            )
        )
    await db_session.commit()

    capped = await GraphTraversalService().traverse(
        session=db_session,
        repository_id=repository.id,
        node_id=root.id,
        direction=TraversalDirection.CALLERS,
        max_nodes=1,
    )
    assert capped is not None
    assert [(n.name, n.kind) for n in capped.nodes] == [("ship", None)]

    every = await GraphTraversalService().traverse(
        session=db_session,
        repository_id=repository.id,
        node_id=root.id,
        direction=TraversalDirection.CALLERS,
    )
    assert every is not None
    assert [(n.name, n.kind) for n in every.nodes] == [
        ("ship", None),
        ("test_pack", "test"),
        ("pack_stub", "generated"),
    ]
