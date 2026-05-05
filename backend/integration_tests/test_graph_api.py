from __future__ import annotations

from sqlalchemy import select

from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import GraphExtractor
from backend.app.graph.parser import GraphParser
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository


async def test_live_postgres_graph_api_serves_list_and_detail(
    integration_client,
    integration_session_manager,
):
    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.READY,
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

    list_response = await integration_client.get(
        f"/api/repos/{repository.id}/graph",
        params={"view": "symbols"},
    )
    detail_response = await integration_client.get(
        f"/api/repos/{repository.id}/graph/nodes/{helper_node.id}"
    )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200

    list_payload = list_response.json()
    detail_payload = detail_response.json()
    assert list_payload["stats"]["total_nodes"] == 4
    assert any(node["name"] == "helper" for node in list_payload["nodes"])
    assert detail_payload["name"] == "helper"
    assert detail_payload["callers"][0]["name"] == "login"
