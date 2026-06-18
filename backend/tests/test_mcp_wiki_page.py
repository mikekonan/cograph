"""MCP `cograph_wiki_page` smoke — full page, single section, misses.

The summarized wiki resource is the default surface; this tool is the
deliberate on-demand pull for one page (or one section) in full. These
tests lock the pull contract: full bodies keep their code/mermaid (unlike
the summarized map), a section read is bounded to that section, and misses
return NOT_FOUND with the available sections listed.
"""

from __future__ import annotations

import hashlib
import json

from backend.app.models.document import Document
from backend.app.models.enums import (
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repository import Repository
from backend.app.models.user import User

_WIKI_DOC_TYPE = "wiki"


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _seed_pat_user(db_session) -> tuple[User, str]:
    user = User(
        email="wiki-page-tool@example.com",
        password_hash="x",
        name="WikiPage",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "w" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="wiki-page-tool",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "mcp"],
        )
    )
    await db_session.commit()
    return user, plaintext


_PAGE_CONTENT = (
    "# Overview\n"
    "A payment platform.\n"
    "## Overview\n"
    "It orchestrates many providers behind one API.\n"
    "## Architecture\n"
    "Layered.\n"
    "```mermaid\nflowchart TD\n  a --> b\n```\n"
    "### Components\n"
    "The terminal builder and the parser.\n"
    "## Configuration\n"
    "Env-driven.\n"
)


async def _seed_repo_with_wiki(db_session) -> Repository:
    repo = Repository(
        host="github.com",
        git_url="git@github.com:acme/widgets.git",
        name="widgets",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    db_session.add(
        Document(
            repository_id=repo.id,
            doc_type=_WIKI_DOC_TYPE,
            title="Overview",
            slug="index",
            sort_order=0,
            content=_PAGE_CONTENT,
            content_hash="content-hash",
            source_hash="source-hash",
            model="gpt-4o-mini",
            citations=[],
            source_node_ids=[],
            source_repo_doc_chunk_ids=[],
            quality={},
        )
    )
    await db_session.commit()
    return repo


async def _call(client, plaintext: str, arguments: dict, *, request_id: int = 1):
    return await client.post(
        "/mcp/",
        headers={
            "Authorization": f"Bearer {plaintext}",
            "Accept": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": "cograph_wiki_page", "arguments": arguments},
        },
    )


async def test_wiki_page_returns_full_body(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    await _seed_repo_with_wiki(db_session)

    response = await _call(
        client, plaintext, {"repository": "github.com/acme/widgets", "page": "index"}
    )

    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["wiki_slug"] == "index"
    assert result["section"] is None
    # Full body VERBATIM: code fences / mermaid are KEPT (the summarized map
    # strips them) AND the markdown structure — newlines, headings, fences —
    # must survive intact, not be flattened to a single line.
    content = result["content"]
    assert "## Architecture\n" in content
    assert "```mermaid\nflowchart TD\n  a --> b\n```" in content
    assert "## Configuration" in content
    assert content.count("\n") >= 8  # not a flattened one-liner
    assert result["content_truncated"] is False
    assert result["tokens_estimate"] > 0


async def test_wiki_page_returns_single_section(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    await _seed_repo_with_wiki(db_session)

    response = await _call(
        client,
        plaintext,
        {
            "repository": "github.com/acme/widgets",
            "page": "index",
            "section": "  architecture  ",  # case/whitespace-insensitive match
        },
    )

    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    # Echoes the CANONICAL heading, not the raw "  architecture  " input.
    assert result["section"] == "Architecture"
    # The section body is verbatim: it starts at its heading, carries its ###
    # subsection and the mermaid block with newlines intact, and stops before
    # the next ## (Configuration must not bleed in).
    content = result["content"]
    assert content.startswith("## Architecture\n")
    assert "```mermaid\nflowchart TD\n  a --> b\n```" in content
    assert "### Components\nThe terminal builder" in content
    assert "Env-driven" not in content


async def test_wiki_page_unknown_section_lists_available(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    await _seed_repo_with_wiki(db_session)

    response = await _call(
        client,
        plaintext,
        {
            "repository": "github.com/acme/widgets",
            "page": "index",
            "section": "Nonexistent",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["isError"] is True
    text = payload["result"]["content"][0]["text"]
    assert "NOT_FOUND" in text
    # The error lists the sections that DO exist so the agent can retry.
    assert "Architecture" in text


async def test_wiki_page_unknown_page_is_not_found(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    await _seed_repo_with_wiki(db_session)

    response = await _call(
        client, plaintext, {"repository": "github.com/acme/widgets", "page": "ghost"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["isError"] is True
    assert "NOT_FOUND" in payload["result"]["content"][0]["text"]
