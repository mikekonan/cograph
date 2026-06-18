"""Tests for the new bootstrap-context MCP resources.

`cograph://briefing` — re-fetchable copy of the operator briefing for
clients whose context got compacted.

`cograph://my-context` — the per-caller ACL-filtered list of repository
slugs and collection ids. The playbook tells the agent to fetch this on
session start so subsequent tool calls use valid `repository=` values.

The MCP server is wired through the FastAPI app's lifespan, so we reach
it via `app.state.mcp_server` from the existing `app` fixture and call
its `read_resource` API directly. That exercises the same code path the
streamable-http transport uses internally; the wire-level transport is
already covered in `test_mcp_transport.py` and doesn't need to be
duplicated here.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from backend.app.models.document import Document
from backend.app.models.enums import (
    MdCollectionVisibility,
    RepositoryStatus,
    RepositoryVisibility,
    UserRole,
)
from backend.app.models.md_collection import MdCollection
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing
from backend.app.models.repository import Repository
from backend.app.models.user import User


def _content_str(read_result) -> str:
    """`FastMCP.read_resource` returns `list[ReadResourceContents]`. We
    only ever return one block from these resources, so unwrap and
    decode UTF-8 if the SDK gave bytes."""
    assert len(read_result) == 1, read_result
    content = read_result[0].content
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return content


async def _get_mcp_server(app):
    return app.state.mcp_server


@pytest.mark.asyncio
async def test_briefing_resource_returns_default_when_empty(app, db_session) -> None:
    # No briefing row exists in the create_all-built test schema; the
    # resource must fall back to DEFAULT_BRIEFING rather than 404 or an
    # empty string. Agents that re-fetch after compaction need *some*
    # cite-or-bust text, not nothing.
    existing = (
        await db_session.execute(select(McpOperatorBriefing))
    ).scalar_one_or_none()
    assert existing is None

    server = await _get_mcp_server(app)
    result = await server.read_resource("cograph://briefing")
    payload = json.loads(_content_str(result))
    assert payload["is_default"] is True
    assert "hasn't been customised yet" in payload["content"]


@pytest.mark.asyncio
async def test_briefing_resource_reflects_admin_edit(app, db_session) -> None:
    db_session.add(
        McpOperatorBriefing(id=1, content="Payments team: ask before currency changes.")
    )
    await db_session.commit()

    server = await _get_mcp_server(app)
    result = await server.read_resource("cograph://briefing")
    payload = json.loads(_content_str(result))
    assert payload["is_default"] is False
    assert "Payments team" in payload["content"]
    assert payload["updated_at"] is not None


@pytest.mark.asyncio
async def test_my_context_returns_empty_lists_for_anonymous_caller(
    app, db_session
) -> None:
    # No `request_ctx` set in this test — `current_user_from_context`
    # returns None, and the read scope falls back to whatever is public.
    # In an empty DB that's still an empty list; the resource must NOT
    # crash on missing auth context.
    server = await _get_mcp_server(app)
    result = await server.read_resource("cograph://my-context")
    payload = json.loads(_content_str(result))
    assert payload["repositories"]["items"] == []
    assert payload["collections"]["items"] == []
    assert payload["repositories"]["total"] == 0
    assert payload["collections"]["total"] == 0


@pytest.mark.asyncio
async def test_my_context_lists_public_repo_and_collection(app, db_session) -> None:
    # Public-visibility records ARE returned to an anonymous caller — the
    # MCP `cograph_repositories` and `cograph_collections` tools already
    # behave this way. The resource just reformats their output.
    repo = Repository(
        host="github.com",
        owner="acme",
        name="payments",
        git_url="https://github.com/acme/payments.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repo)

    owner = User(
        email="owner@example.com",
        password_hash="$2b$12$placeholderplaceholderplaceholderplaceholderplaceholder",
        role=UserRole.USER,
    )
    db_session.add(owner)
    await db_session.flush()

    collection = MdCollection(
        owner_id=owner.id,
        name="Engineering glossary",
        description="Acronyms and domain terms.",
        visibility=MdCollectionVisibility.PUBLIC,
    )
    db_session.add(collection)
    await db_session.commit()

    server = await _get_mcp_server(app)
    result = await server.read_resource("cograph://my-context")
    payload = json.loads(_content_str(result))

    repo_slugs = [item["slug"] for item in payload["repositories"]["items"]]
    assert "github.com/acme/payments" in repo_slugs
    # A repo without any generated wiki must surface wiki_total: 0 so the
    # agent can see the Wiki-FIRST rule (Step 1 in the playbook) does NOT apply
    # to it. Absence of the field would force the agent to guess.
    repo_entry = next(
        item
        for item in payload["repositories"]["items"]
        if item["slug"] == "github.com/acme/payments"
    )
    assert repo_entry["wiki_total"] == 0

    collection_names = [item["name"] for item in payload["collections"]["items"]]
    assert "Engineering glossary" in collection_names


@pytest.mark.asyncio
async def test_my_context_reports_wiki_total_when_pages_exist(
    app, db_session
) -> None:
    # The playbook's Wiki gate fires only on repos with wiki_total > 0; the
    # session-bootstrap resource must therefore report a non-zero count when
    # the repo actually has generated pages. Two pages, expect 2.
    repo = Repository(
        host="github.com",
        owner="acme",
        name="runner",
        git_url="https://github.com/acme/runner.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repo)
    await db_session.flush()

    for idx, slug in enumerate(("overview", "architecture")):
        db_session.add(
            Document(
                repository_id=repo.id,
                slug=slug,
                title=slug.capitalize(),
                doc_type="wiki",
                sort_order=idx,
                content=f"# {slug}\n\nGenerated page body.",
                content_hash=f"hash-{slug}",
                source_hash=f"src-{slug}",
                model="test",
            )
        )
    await db_session.commit()

    server = await _get_mcp_server(app)
    result = await server.read_resource("cograph://my-context")
    payload = json.loads(_content_str(result))

    repo_entry = next(
        item
        for item in payload["repositories"]["items"]
        if item["slug"] == "github.com/acme/runner"
    )
    assert repo_entry["wiki_total"] == 2


@pytest.mark.asyncio
async def test_wiki_tree_resource_serves_compacted_wiki(app, db_session) -> None:
    # The wiki tree resource is the served wiki: alongside the navigation
    # tree it must carry the compacted map (lead + sections + covered
    # questions per page) so an MCP client gets the wiki content here, not
    # just a list of titles.
    repo = Repository(
        host="github.com",
        owner="acme",
        name="kms",
        git_url="https://github.com/acme/kms.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repo)
    await db_session.flush()

    db_session.add(
        Document(
            repository_id=repo.id,
            slug="index",
            title="Overview",
            doc_type="wiki",
            sort_order=0,
            content=(
                "# Overview\n"
                "A key-management service.\n"
                "```go\n"
                "func main() {}\n"
                "```\n"
                "## What it does\n"
            ),
            content_hash="hash-index",
            source_hash="src-index",
            model="test",
            quality={"covers_questions": ["use-cases"]},
        )
    )
    await db_session.commit()

    server = await _get_mcp_server(app)
    result = await server.read_resource("cograph://repo/github.com/acme/kms/wiki")
    payload = json.loads(_content_str(result))

    assert payload["total"] == 1
    assert "compact" in payload
    entry = payload["compact"][0]
    assert entry["slug"] == "index"
    assert entry["lead"] == "A key-management service."
    assert "func main" not in entry["lead"]  # code fence stripped
    assert entry["sections"] == ["What it does"]
    assert entry["covers_questions"] == ["use-cases"]
    # Summarized is the DEFAULT surface: the payload marks itself as such and
    # points at the pull-only tool for full bodies — but advertises no per-page
    # resource URI for agents to follow, nor the whole-repo graph snapshot
    # (a 40-60k-token dump removed from MCP).
    assert payload["wiki_form"] == "summarized"
    assert payload["full_page_tool"] == "cograph_wiki_page"
    assert "cograph_wiki_page" in payload["hint"]
    assert "page_template" not in payload["resources"]
    assert "page" not in payload["resources"]
    assert "graph" not in payload["resources"]


@pytest.mark.asyncio
async def test_wiki_page_resource_is_not_served_over_mcp(app, db_session) -> None:
    # Full pages are reachable only via the cograph_wiki_page TOOL, never as a
    # resource URI: there is no per-page resource, so reading its old URI must
    # fail (the summarized map stays the only advertised wiki resource).
    repo = Repository(
        host="github.com",
        owner="acme",
        name="kms",
        git_url="https://github.com/acme/kms.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repo)
    await db_session.flush()
    db_session.add(
        Document(
            repository_id=repo.id,
            slug="index",
            title="Overview",
            doc_type="wiki",
            sort_order=0,
            content="# Overview\nFull prose agents must not receive.\n",
            content_hash="hash-index",
            source_hash="src-index",
            model="test",
            quality={},
        )
    )
    await db_session.commit()

    server = await _get_mcp_server(app)
    with pytest.raises(Exception):
        await server.read_resource("cograph://repo/github.com/acme/kms/wiki/index")


@pytest.mark.asyncio
async def test_graph_resources_are_not_served_over_mcp(app, db_session) -> None:
    # The whole-repo graph snapshot (up to 1000 nodes ≈ 40-60k tokens) and
    # the per-node graph resource were removed from MCP: nothing in the
    # playbook recommends them, and agents traverse via the capped
    # cograph_related tool instead. Both old URIs must be unreadable.
    repo = Repository(
        host="github.com",
        owner="acme",
        name="kms",
        git_url="https://github.com/acme/kms.git",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repo)
    await db_session.commit()

    server = await _get_mcp_server(app)
    with pytest.raises(Exception):
        await server.read_resource("cograph://repo/github.com/acme/kms/graph")
    with pytest.raises(Exception):
        await server.read_resource(
            "cograph://repo/github.com/acme/kms/graph/node/"
            "00000000-0000-0000-0000-000000000000"
        )
