"""MCP `cograph_repository_readme` smoke — README hit, wiki fallback, miss."""

from __future__ import annotations

import hashlib
import json

from backend.app.models.enums import (
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repo_document import RepoDocument
from backend.app.models.repository import Repository
from backend.app.models.user import User

_WIKI_DOC_TYPE = "wiki"


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _seed_pat_user(db_session) -> tuple[User, str]:
    user = User(
        email="readme-tool@example.com",
        password_hash="x",
        name="Readme",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "r" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="readme-tool",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "mcp"],
        )
    )
    await db_session.commit()
    return user, plaintext


async def _seed_repository(db_session) -> Repository:
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
    return repo


async def _call_tool(client, plaintext: str, slug: str, *, request_id: int = 1):
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
            "params": {
                "name": "cograph_repository_readme",
                "arguments": {"slug": slug},
            },
        },
    )


async def test_repository_readme_returns_indexed_readme(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    repo = await _seed_repository(db_session)
    db_session.add(
        RepoDocument(
            repository_id=repo.id,
            file_path="README.md",
            title="widgets",
            content="# widgets\n\nA short description of the widgets project.",
            content_hash="hash-readme",
            bytes=58,
        )
    )
    await db_session.commit()

    response = await _call_tool(client, plaintext, "github.com/acme/widgets")

    assert response.status_code == 200
    payload = response.json()
    result = json.loads(payload["result"]["content"][0]["text"])
    assert result["source"] == "repo_doc"
    assert result["source_path"] == "README.md"
    assert result["repository_slug"] == "github.com/acme/widgets"
    assert "widgets project" in result["content"]
    assert result["content_truncated"] is False


async def test_repository_readme_picks_longest_when_multiple_matches(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    repo = await _seed_repository(db_session)
    long_content = "Detailed description.\n" * 50
    db_session.add_all(
        [
            RepoDocument(
                repository_id=repo.id,
                file_path="docs/readme-stub.md",
                title="stub",
                content="stub",
                content_hash="stub",
                bytes=len("stub"),
            ),
            RepoDocument(
                repository_id=repo.id,
                file_path="README.md",
                title="canonical",
                content=long_content,
                content_hash="canonical",
                bytes=len(long_content),
            ),
        ]
    )
    await db_session.commit()

    response = await _call_tool(client, plaintext, "github.com/acme/widgets")

    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["source_path"] == "README.md"


async def test_repository_readme_falls_back_to_compacted_wiki_overview(client, db_session):
    # The fallback must serve the COMPACT form (lead + section headings),
    # never the full page body — full generated-wiki prose is deliberately
    # unreachable over MCP.
    _, plaintext = await _seed_pat_user(db_session)
    repo = await _seed_repository(db_session)

    from backend.app.models.document import Document

    db_session.add(
        Document(
            repository_id=repo.id,
            doc_type=_WIKI_DOC_TYPE,
            title="Overview",
            slug="overview",
            content=(
                "# Overview\n\n"
                "Project summary indexed by the wiki agent.\n"
                "```go\nfunc main() {}\n```\n"
                "## Architecture\n"
                "Deep prose the agent must not receive.\n"
            ),
            content_hash="content-hash",
            source_hash="source-hash",
            sort_order=0,
            model="gpt-4o-mini",
            citations=[],
            source_node_ids=[],
            source_repo_doc_chunk_ids=[],
            quality={},
        )
    )
    await db_session.commit()

    response = await _call_tool(client, plaintext, "github.com/acme/widgets")

    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["source"] == "wiki"
    assert result["wiki_slug"] == "overview"
    assert "Project summary" in result["lead"]
    assert result["sections"] == ["Architecture"]
    # Compact-only: no full body, no code fences, no section prose.
    assert "content" not in result
    assert "func main" not in json.dumps(result)
    assert "Deep prose" not in json.dumps(result)


async def test_repository_readme_returns_not_found_when_nothing_indexed(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    await _seed_repository(db_session)

    response = await _call_tool(client, plaintext, "github.com/acme/widgets")

    assert response.status_code == 200
    payload = response.json()
    # FastMCP serialises tool ValueError into an error result.
    assert payload["result"]["isError"] is True
    assert "NOT_FOUND" in payload["result"]["content"][0]["text"]
