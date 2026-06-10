from __future__ import annotations

from sqlalchemy import select, text

from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import GraphExtractor
from backend.app.graph.parser import GraphParser
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository


async def test_graph_builder_persists_code_nodes_on_live_postgres(
    integration_session_manager,
):
    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            host="example.com",
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

        result = await GraphBuilder().persist_graph(
            session=session,
            repository_id=repository.id,
            extracted_graph=extracted_graph,
        )
        await session.commit()

        assert result.inserted_nodes == 5
        assert result.resolved_calls == 2

        login_node = await session.scalar(
            select(CodeNode).where(
                CodeNode.qualified_name == "service.UserService.login"
            )
        )
        helper_node = await session.scalar(
            select(CodeNode).where(CodeNode.qualified_name == "service.helper")
        )

    assert login_node is not None
    assert helper_node is not None
    assert str(helper_node.id) in login_node.callees


async def test_live_postgres_graph_builder_rebinds_cross_file_calls_after_reindex(
    integration_session_manager,
):
    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            host="example.com",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repository)
        await session.flush()

        parser = GraphParser()
        extractor = GraphExtractor()
        builder = GraphBuilder()

        caller_graph = extractor.extract(
            parser.parse_source(
                file_path="b.py",
                source_text="import a\n\ndef call() -> int:\n    return a.helper()\n",
            )
        )
        helper_graph = extractor.extract(
            parser.parse_source(
                file_path="a.py",
                source_text="def helper() -> int:\n    return 1\n",
            )
        )

        await builder.persist_graph(
            session=session,
            repository_id=repository.id,
            extracted_graph=caller_graph,
        )
        await builder.persist_graph(
            session=session,
            repository_id=repository.id,
            extracted_graph=helper_graph,
        )
        await session.commit()

        original_helper = await session.scalar(
            select(CodeNode).where(CodeNode.qualified_name == "a.helper")
        )
        caller_node = await session.scalar(
            select(CodeNode).where(CodeNode.qualified_name == "b.call")
        )
        assert original_helper is not None
        assert caller_node is not None
        assert caller_node.callees == [str(original_helper.id)]

        updated_helper_graph = extractor.extract(
            parser.parse_source(
                file_path="a.py",
                source_text="def helper() -> int:\n    return 2\n",
            )
        )
        await builder.persist_graph(
            session=session,
            repository_id=repository.id,
            extracted_graph=updated_helper_graph,
        )
        await session.commit()

        rebound_helper = await session.scalar(
            select(CodeNode).where(CodeNode.qualified_name == "a.helper")
        )
        rebound_caller = await session.scalar(
            select(CodeNode).where(CodeNode.qualified_name == "b.call")
        )

    assert rebound_helper is not None
    assert rebound_caller is not None
    # persist_graph updates existing nodes in-place (preserving id), so id stays stable
    assert rebound_helper.id == original_helper.id
    assert rebound_caller.callees == [str(rebound_helper.id)]
    assert rebound_helper.callers == [str(rebound_caller.id)]


async def test_live_postgres_head_includes_code_nodes_table(
    integration_session_manager,
):
    async with integration_session_manager.engine.connect() as connection:
        tables = await connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                AND tablename = 'code_nodes'
                """
            )
        )

    assert list(tables.scalars()) == ["code_nodes"]
