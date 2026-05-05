from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import GraphExtractor
from backend.app.graph.parser import GraphParser
from backend.app.graph.queries import GraphQueryFilters, GraphQueryService, GraphView
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository


async def _persist_go_sources(
    *,
    db_session,
    repository_id,
    sources: list[tuple[str, str]],
    go_module_path: str,
) -> None:
    parser = GraphParser()
    extractor = GraphExtractor()
    builder = GraphBuilder()
    for file_path, source_text in sources:
        extracted = extractor.extract(
            parser.parse_source(file_path=file_path, source_text=source_text),
            go_module_path=go_module_path,
        )
        await builder.persist_graph(
            session=db_session,
            repository_id=repository_id,
            extracted_graph=extracted,
        )
    await db_session.commit()


async def _persist_go_fixture_repo(
    *,
    db_session,
    repository_id,
    fixture_root: Path,
    go_module_path: str,
) -> None:
    sources = [
        (
            path.relative_to(fixture_root).as_posix(),
            path.read_text(),
        )
        for path in sorted(fixture_root.rglob("*.go"))
    ]
    await _persist_go_sources(
        db_session=db_session,
        repository_id=repository_id,
        sources=sources,
        go_module_path=go_module_path,
    )


async def test_graph_query_service_lists_architecture_nodes_and_edges(db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    extracted = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="service.py",
            source_text="""
class BaseService:
    pass


class UserService(BaseService):
    def login(self, user_id: str) -> bool:
        return helper(user_id)


def helper(user_id: str) -> str:
    return user_id
""",
        )
    )
    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
    )
    await db_session.commit()

    result = await GraphQueryService().list_graph(
        session=db_session,
        repository_id=repository.id,
        filters=GraphQueryFilters(view=GraphView.ARCHITECTURE),
    )

    assert [
        node.qualified_name if hasattr(node, "qualified_name") else node.name
        for node in result.nodes
    ] == [
        "service",
        "BaseService",
        "UserService",
    ]
    assert result.stats.total_nodes == 5
    assert result.stats.matched_nodes == 3
    assert result.stats.returned_nodes == 3
    assert result.stats.languages == {"python": 5}
    assert {edge.edge_type.value for edge in result.edges} == {"declares", "inherits"}
    assert len(result.edges) == 3


async def test_graph_query_service_returns_node_detail_with_relations(db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    extracted = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="service.py",
            source_text="""
class UserService:
    def audit(self, user_id: str) -> None:
        return None

    def login(self, user_id: str) -> bool:
        self.audit(user_id)
        helper(user_id)
        return True


def helper(user_id: str) -> str:
    return user_id
""",
        )
    )
    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
    )
    await db_session.commit()

    login_node = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "service.UserService.login")
    )
    class_node = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "service.UserService")
    )
    assert login_node is not None
    assert class_node is not None

    login_detail = await GraphQueryService().get_node_detail(
        session=db_session,
        repository_id=repository.id,
        node_id=login_node.id,
    )
    class_detail = await GraphQueryService().get_node_detail(
        session=db_session,
        repository_id=repository.id,
        node_id=class_node.id,
    )

    assert login_detail is not None
    assert login_detail.parent is not None
    assert login_detail.parent.name == "UserService"
    assert [node.name for node in login_detail.callees] == ["audit", "helper"]
    assert login_detail.callers == []

    assert class_detail is not None
    assert class_detail.parent is not None
    assert class_detail.parent.node_type is CodeNodeType.MODULE
    assert [member.name for member in class_detail.members] == ["audit", "login"]


async def test_graph_query_service_v2_read_gate_uses_source_file_slice(db_session):
    # Regression for C4: graph_storage_version must actually gate the read
    # path. With version=2 we derive `content` from source_files byte range
    # and `callers`/`callees` from code_edges, not from the legacy arrays.
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    extracted = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="m.py",
            source_text="def helper() -> int:\n    return 1\n\ndef caller() -> int:\n    return helper()\n",
        )
    )
    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted,
    )
    await db_session.commit()

    caller_node = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "m.caller")
    )
    assert caller_node is not None

    # Simulate cutover: flip to v2 AND wipe the legacy arrays so we can prove
    # the v2 read path doesn't just lean on them.
    repository.graph_storage_version = 2
    caller_node.callees = []
    caller_node.callers = []
    caller_node.content = "<<stale-legacy-content>>"
    await db_session.commit()

    detail = await GraphQueryService().get_node_detail(
        session=db_session,
        repository_id=repository.id,
        node_id=caller_node.id,
    )
    assert detail is not None
    # Content is freshly sliced from source_files.raw_bytes, not the legacy
    # stale string we wrote above.
    assert "return helper()" in detail.content
    assert "<<stale-legacy-content>>" not in detail.content
    # Callees come from code_edges even though caller_node.callees is empty.
    assert [c.name for c in detail.callees] == ["helper"]


