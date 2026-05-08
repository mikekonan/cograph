"""MCP `cograph.outline` smoke — repo + collection variants."""

from __future__ import annotations

import hashlib
import json

from backend.app.models.enums import (
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.md_collection import MdCollection, MdDocument
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile
from backend.app.models.user import User


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _seed_pat_user(db_session) -> tuple[User, str]:
    user = User(
        email="outline-tool@example.com",
        password_hash="x",
        name="Outline",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "o" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="outline-tool",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "mcp"],
        )
    )
    await db_session.commit()
    return user, plaintext


async def _call_tool(client, plaintext: str, args: dict, *, request_id: int = 1):
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
                "name": "cograph.outline",
                "arguments": args,
            },
        },
    )


async def test_outline_returns_repo_top_dirs_and_wiki_titles(client, db_session):
    user, plaintext = await _seed_pat_user(db_session)
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
    await db_session.flush()

    paths = [
        "src/main.py",
        "src/util.py",
        "src/lib/inner.py",
        "tests/test_main.py",
        "tests/test_util.py",
        "README.md",
    ]
    for path in paths:
        raw = b"x"
        db_session.add(
            SourceFile(
                repository_id=repo.id,
                file_path=path,
                language="python" if path.endswith(".py") else "markdown",
                kind="code" if path.endswith(".py") else "markdown",
                raw_bytes=raw,
                content_hash=hashlib.sha256(path.encode()).hexdigest(),
                bytes=len(raw),
            )
        )

    from backend.app.models.document import Document

    db_session.add_all(
        [
            Document(
                repository_id=repo.id,
                doc_type="wiki",
                title="Overview",
                slug="overview",
                content="# Overview\n",
                content_hash="ov",
                source_hash="ov",
                sort_order=0,
                model="gpt-4o-mini",
                citations=[],
                source_node_ids=[],
                source_repo_doc_chunk_ids=[],
                quality={},
            ),
            Document(
                repository_id=repo.id,
                doc_type="wiki",
                title="Architecture",
                slug="architecture",
                content="# Architecture\n",
                content_hash="arch",
                source_hash="arch",
                sort_order=1,
                model="gpt-4o-mini",
                citations=[],
                source_node_ids=[],
                source_repo_doc_chunk_ids=[],
                quality={},
            ),
        ]
    )
    await db_session.commit()

    response = await _call_tool(
        client,
        plaintext,
        {"repository": "github.com/acme/widgets"},
    )

    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["kind"] == "repository"
    assert result["repository_slug"] == "github.com/acme/widgets"
    dirs = {item["path"]: item["file_count"] for item in result["top_directories"]}
    assert dirs["src"] == 3
    assert dirs["tests"] == 2
    assert dirs["README.md"] == 1
    titles = {page["slug"] for page in result["wiki_pages"]}
    assert titles == {"overview", "architecture"}
    assert result["wiki_total"] == 2


async def test_outline_returns_collection_documents_and_headings(
    client, db_session
):
    user, plaintext = await _seed_pat_user(db_session)
    collection = MdCollection(
        name="my-notes",
        description="Notes",
        owner_id=user.id,
        visibility="private",
    )
    db_session.add(collection)
    await db_session.flush()
    db_session.add_all(
        [
            MdDocument(
                collection_id=collection.id,
                source_key="a.md",
                title="A",
                content="# A\n## A.1\n",
                content_hash="a",
                bytes=10,
                word_count=2,
                line_count=3,
                frontmatter={},
                heading_tree=[
                    {
                        "level": 1,
                        "text": "A",
                        "children": [
                            {"level": 2, "text": "A.1", "children": []},
                        ],
                    }
                ],
                code_blocks=[],
                tables=[],
                links=[],
            ),
            MdDocument(
                collection_id=collection.id,
                source_key="b.md",
                title="B",
                content="# B\n",
                content_hash="b",
                bytes=4,
                word_count=1,
                line_count=1,
                frontmatter={},
                heading_tree=[
                    {"level": 1, "text": "B", "children": []},
                ],
                code_blocks=[],
                tables=[],
                links=[],
            ),
        ]
    )
    await db_session.commit()

    response = await _call_tool(
        client,
        plaintext,
        {"collection_id": str(collection.id)},
    )

    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["kind"] == "collection"
    assert result["collection_id"] == str(collection.id)
    assert result["documents_total"] == 2
    keys = {doc["source_key"] for doc in result["documents"]}
    assert keys == {"a.md", "b.md"}
    a_doc = next(doc for doc in result["documents"] if doc["source_key"] == "a.md")
    headings = [(h["level"], h["text"]) for h in a_doc["headings"]]
    assert headings == [(1, "A"), (2, "A.1")]


async def test_outline_rejects_both_args_missing(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)

    response = await _call_tool(client, plaintext, {})

    payload = response.json()
    assert payload["result"]["isError"] is True
    assert "INVALID_REQUEST" in payload["result"]["content"][0]["text"]


async def test_outline_rejects_both_args_present(client, db_session):
    user, plaintext = await _seed_pat_user(db_session)
    collection = MdCollection(
        name="dual",
        description=None,
        owner_id=user.id,
        visibility="private",
    )
    db_session.add(collection)
    await db_session.commit()

    response = await _call_tool(
        client,
        plaintext,
        {
            "repository": "github.com/acme/widgets",
            "collection_id": str(collection.id),
        },
    )

    payload = response.json()
    assert payload["result"]["isError"] is True
    assert "INVALID_REQUEST" in payload["result"]["content"][0]["text"]
