from __future__ import annotations

from sqlalchemy import select

from backend.app.graph.builder import GraphBuilder
from backend.app.graph.extractor import GraphExtractor
from backend.app.graph.parser import GraphParser
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus, RepositoryVisibility, SyncSchedule
from backend.app.models.repository import Repository


async def test_graph_list_endpoint_returns_repo_graph(client, db_session):
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
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted_graph,
    )
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/graph",
        params={"view": "symbols", "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    # Stats include all nodes (unfiltered total)
    assert payload["stats"]["total_nodes"] == 4
    assert payload["stats"]["matched_nodes"] == 4
    assert payload["stats"]["returned_nodes"] == 4
    assert payload["stats"]["languages"] == {"python": 4}
    # After server-side filtering: only FE-known node types (module, class, function, method)
    # are returned. "service" is module, "UserService" is class, "login"/"helper" are function.
    returned_names = {node["name"] for node in payload["nodes"]}
    assert returned_names == {"service", "UserService", "login", "helper"}
    assert all(node["complexity"] == 0 for node in payload["nodes"])
    # "declares" edges must NOT appear in the response (filtered server-side per FE contract)
    edge_types = {edge["type"] for edge in payload["edges"]}
    assert "declares" not in edge_types
    assert "calls" in edge_types


async def test_graph_detail_endpoint_returns_node_relationships(client, db_session):
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
    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted_graph,
    )
    await db_session.commit()

    login_node = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "service.UserService.login")
    )
    assert login_node is not None

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/graph/nodes/{login_node.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "login"
    assert payload["parent"] == {
        "id": str(
            (
                await db_session.scalar(
                    select(CodeNode.id).where(CodeNode.qualified_name == "service.UserService")
                )
            )
        ),
        "name": "UserService",
        "node_type": "class",
    }
    assert [node["name"] for node in payload["callees"]] == ["audit", "helper"]
    assert payload["callers"] == []
    assert payload["complexity"] == 0


async def test_graph_endpoint_returns_empty_for_repo_not_ready(client, db_session):
    """Graph list returns empty 200 when repo is still indexing (not 409) per FE contract."""
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
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/graph")

    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["stats"]["total_nodes"] == 0
    assert payload["stats"]["matched_nodes"] == 0
    assert payload["stats"]["returned_nodes"] == 0


async def test_graph_endpoint_hides_admin_only_repo_from_anonymous(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/graph")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_graph_detail_returns_not_found_for_unknown_node(client, db_session):
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
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/graph/nodes/00000000-0000-0000-0000-000000000001"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_graph_detail_hides_admin_only_repo_from_anonymous(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    extracted_graph = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="service.py",
            source_text="""
def helper(user_id: str) -> str:
    return user_id
""",
        )
    )
    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted_graph,
    )
    await db_session.commit()

    helper_node = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "service.helper")
    )
    assert helper_node is not None

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/graph/nodes/{helper_node.id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_graph_node_by_qualified_name_returns_current_uuid(client, db_session):
    """Wiki citation fallback path: persisted markdown ships frozen
    UUIDs in hrefs; if the symbol was renamed/moved post-generation,
    the FE retries against this resolver with the citation's
    `qualified_name` to pick up the current UUID."""
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

    extracted_graph = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="service.py",
            source_text="""
class UserService:
    def login(self, user_id: str) -> bool:
        return user_id


def helper(user_id: str) -> str:
    return user_id
""",
        )
    )
    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted_graph,
    )
    await db_session.commit()

    helper_node = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "service.helper")
    )
    assert helper_node is not None

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/graph/nodes/by-qn/service.helper"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(helper_node.id)
    assert payload["name"] == "helper"


async def test_graph_node_by_qualified_name_404_for_unknown(client, db_session):
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
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/graph/nodes/by-qn/never.existed.Anywhere"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_graph_nodes_check_partitions_existing_and_stale(client, db_session):
    """Wiki page-load batches all `kind=node` citation IDs to compute a
    stale-citation chip. Endpoint must return the partition without
    raising on UUIDs that no longer exist in `code_nodes`."""
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

    extracted_graph = GraphExtractor().extract(
        GraphParser().parse_source(
            file_path="service.py",
            source_text="""
def helper(user_id: str) -> str:
    return user_id
""",
        )
    )
    await GraphBuilder().persist_graph(
        session=db_session,
        repository_id=repository.id,
        extracted_graph=extracted_graph,
    )
    await db_session.commit()

    helper_node = await db_session.scalar(
        select(CodeNode).where(CodeNode.qualified_name == "service.helper")
    )
    assert helper_node is not None
    fake = "00000000-0000-0000-0000-000000000099"

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/graph/nodes/check",
        json={"node_ids": [str(helper_node.id), fake]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] == [str(helper_node.id)]
    assert payload["stale"] == [fake]


async def test_graph_nodes_check_empty_request(client, db_session):
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
    await db_session.commit()

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/graph/nodes/check",
        json={"node_ids": []},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": [], "stale": []}
