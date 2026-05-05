"""HTTP tests for the wiki API surface (`/api/repos/.../wiki/...`)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.models.code_node import CodeNode
from backend.app.models.document import Document
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository

pytestmark = pytest.mark.asyncio


async def _seed_ready_repo(db_session) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/acme/widget",
        name="widget",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _seed_node(db_session, *, repo_id, qn: str) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path="src/pipeline.py",
        qualified_name=qn,
        node_type=CodeNodeType.FUNCTION,
        name=qn.rsplit(".", 1)[-1],
        language="python",
        start_line=10,
        end_line=42,
        content="def fn(): pass\n",
        content_hash="c" * 64,
    )
    db_session.add(node)
    await db_session.flush()
    return node


async def test_repair_citations_endpoint_upgrades_uuid_form_to_slug(
    client, db_session
):
    repo = await _seed_ready_repo(db_session)
    node = await _seed_node(db_session, repo_id=repo.id, qn="pkg.Live")
    page = Document(
        repository_id=repo.id,
        slug="overview",
        title="Overview",
        doc_type="wiki",
        sort_order=0,
        content=(
            f"[`pkg.Live`](/repos/{repo.id}/graph?node={node.id}) is the export.\n"
        ),
        content_hash="h" * 64,
        source_hash="s" * 64,
        model="wiki-llm-v1",
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        citations=[
            {
                "id": str(node.id),
                "kind": "node",
                "label": "Live",
                "file_path": "src/pipeline.py",
            }
        ],
    )
    db_session.add(page)
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/wiki/overview/repair-citations"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "patched": 0,
        "dropped": 0,
        "unchanged": 1,
        "url_format_upgraded": 1,
        "raced": False,
    }


async def test_repair_citations_endpoint_404_for_missing_page(
    client, db_session
):
    repo = await _seed_ready_repo(db_session)
    await db_session.commit()
    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/wiki/nonexistent/repair-citations"
    )
    assert response.status_code == 404


async def test_repair_citations_endpoint_404_for_unknown_repo(client):
    response = await client.post(
        f"/api/repos/example.com/ghost/{uuid4().hex}/wiki/overview/repair-citations"
    )
    assert response.status_code == 404
