from __future__ import annotations

from sqlalchemy import select

from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import GraphExtractor
from backend.app.graph.parser import GraphParser
from backend.app.graph.queries import GraphQueryFilters, GraphQueryService, GraphView
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository


async def test_graph_query_service_reads_live_postgres_graph(
    integration_session_manager,
):
    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repository)
        await session.flush()

        extracted_graph = GraphExtractor().extract(
            GraphParser().parse_source(
                file_path="service.py",
                source_text="""
class UserService:
    def login(self, user_id: str) -> bool:
        return helper(user_id)


def helper(user_id: str) -> str:
    return user_id
""",
            )
        )
        await GraphBuilder().persist_graph(
            session=session,
            repository_id=repository.id,
            extracted_graph=extracted_graph,
        )
        await session.commit()

        helper_node = await session.scalar(
            select(CodeNode).where(CodeNode.qualified_name == "service.helper")
        )
        assert helper_node is not None

        graph_result = await GraphQueryService().list_graph(
            session=session,
            repository_id=repository.id,
            filters=GraphQueryFilters(view=GraphView.SYMBOLS),
        )
        helper_detail = await GraphQueryService().get_node_detail(
            session=session,
            repository_id=repository.id,
            node_id=helper_node.id,
        )

    assert graph_result.stats.total_nodes == 4
    assert any(node.name == "helper" for node in graph_result.nodes)
    assert helper_detail is not None
    assert [caller.name for caller in helper_detail.callers] == ["login"]
