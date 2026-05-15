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
    # MCP `cograph.repositories` and `cograph.collections` tools already
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

    collection_names = [item["name"] for item in payload["collections"]["items"]]
    assert "Engineering glossary" in collection_names
