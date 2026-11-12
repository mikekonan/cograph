"""`cograph_search_code` names the other repositories that hold a name from
the query, so an agent can follow a value into the service that decides it.

SQLite: the document lookup is the substring fallback. The `content_tsv`
path runs in `integration_tests/test_mcp_hit_shape.py`.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.services import count_response_results
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, RepositoryVisibility, UserRole
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.rag.source_router import names_elsewhere
from backend.tests.test_route_tool import _make_user


async def _repo(db_session, name: str, *, public: bool = True) -> Repository:
    repo = Repository(
        host="git.example.com",
        owner="team",
        name=name,
        git_url=f"https://git.example.com/team/{name}.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC if public else RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


def _node(repo: Repository, name: str) -> CodeNode:
    return CodeNode(
        repository_id=repo.id,
        file_path=f"src/{name}.go",
        qualified_name=f"src.{name}",
        name=name,
        language="go",
        node_type=CodeNodeType.FUNCTION,
        start_line=1,
        end_line=9,
        content=f"func {name}() {{}}\n",
        content_hash=uuid.uuid4().hex,
    )


async def _document(db_session, repo: Repository, path: str, *chunks) -> None:
    """`chunks`: (text, start_line, end_line) in chunk order."""
    doc = RepoDocument(
        repository_id=repo.id,
        file_path=path,
        title=path,
        content="".join(text for text, _, _ in chunks),
        content_hash="d" * 64,
        bytes=1,
    )
    db_session.add(doc)
    await db_session.flush()
    for index, (text, start, end) in enumerate(chunks):
        db_session.add(
            RepoDocumentChunk(
                document_id=doc.id,
                chunk_index=index,
                heading_path=[],
                content=text,
                mentions=[],
                start_line=start,
                end_line=end,
            )
        )


@pytest.mark.asyncio
async def test_other_readable_repositories_holding_the_name_are_listed(
    db_session, settings
) -> None:
    warehouse = await _repo(db_session, "warehouse")
    shipping = await _repo(db_session, "shipping")
    handbook = await _repo(db_session, "handbook")
    tracking = await _repo(db_session, "tracking")
    audit = await _repo(db_session, "audit", public=False)
    # The searched repository and a hidden one hold the name too.
    db_session.add_all([_node(r, "OrderShipped") for r in (warehouse, shipping, tracking, audit)])
    await _document(db_session, warehouse, "docs/events.md", ("OrderShipped is sent.", None, None))
    # The first chunk by path, then by chunk index, gives the lines.
    await _document(db_session, handbook, "notes/later.md", ("Then OrderShipped.", None, None))
    await _document(
        db_session,
        handbook,
        "flows/orders.yml",
        ("pack:\n  next: label\n", 1, 2),
        ("label:\n  onDone: OrderShipped\n", 3, 5),
        ("notify:\n  on: OrderShipped\n", 9, 12),
    )
    await _document(db_session, tracking, "docs/events.md", ("Emits OrderShipped.", None, None))
    await db_session.commit()

    async def elsewhere(user):
        return await names_elsewhere(
            db_session,
            query="where is OrderShipped sent",
            repository_id=warehouse.id,
            current_user=user,
            settings=settings,
        )

    found = await elsewhere(None)
    assert found == {
        "elsewhere": [
            {
                "repository": "git.example.com/team/tracking",
                "in": "code+document",
                "at": "docs/events.md",
            },
            {
                "repository": "git.example.com/team/handbook",
                "in": "document",
                "at": "flows/orders.yml:3-5",
            },
            {"repository": "git.example.com/team/shipping", "in": "code"},
        ]
    }
    # Not a count of hits: `result_count` stays the number of results.
    assert count_response_results({"results": [], **found}) == 0
    # The hidden repository is there for a caller who can read it.
    admin = await _make_user(db_session, email="admin@example.com", role=UserRole.ADMIN)
    shown = [e["repository"] for e in (await elsewhere(admin))["elsewhere"]]
    assert "git.example.com/team/audit" in shown


@pytest.mark.asyncio
async def test_past_five_repositories_the_rest_are_counted(db_session, settings) -> None:
    searched = await _repo(db_session, "searched")
    for index in range(7):
        db_session.add(_node(await _repo(db_session, f"store-{index}"), "restockShelfIfEmpty"))
    await db_session.commit()

    found = await names_elsewhere(
        db_session,
        query="restockShelfIfEmpty",
        repository_id=searched.id,
        current_user=None,
        settings=settings,
    )

    assert [e["repository"] for e in found["elsewhere"]] == [
        f"git.example.com/team/store-{index}" for index in range(5)
    ]
    assert found["elsewhere_more"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["restock the shelf", "restock_shelf_if_empty", "Shipment", "YAML", "HTTP2"]
)
async def test_a_query_without_a_code_name_runs_no_lookup(settings, query) -> None:
    # No session: a lookup would fail on it.
    assert (
        await names_elsewhere(
            None, query=query, repository_id=uuid.uuid4(), current_user=None, settings=settings
        )
        == {}
    )
