"""MCP `cograph.read_file_range` smoke — slice, range guard, end-clamp."""

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
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile
from backend.app.models.user import User


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _seed_pat_user(db_session) -> tuple[User, str]:
    user = User(
        email="rfr-tool@example.com",
        password_hash="x",
        name="Range",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "f" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="rfr-tool",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "mcp"],
        )
    )
    await db_session.commit()
    return user, plaintext


async def _seed_repo_with_file(db_session, *, content: str) -> tuple[Repository, str]:
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
    raw = content.encode("utf-8")
    db_session.add(
        SourceFile(
            repository_id=repo.id,
            file_path="src/main.py",
            language="python",
            kind="code",
            raw_bytes=raw,
            content_hash=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
        )
    )
    await db_session.commit()
    await db_session.refresh(repo)
    return repo, "github.com/acme/widgets"


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
                "name": "cograph.read_file_range",
                "arguments": args,
            },
        },
    )


async def test_read_file_range_returns_slice(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    content = "\n".join(f"line-{i}" for i in range(1, 21))
    _, slug = await _seed_repo_with_file(db_session, content=content)

    response = await _call_tool(
        client,
        plaintext,
        {"repository": slug, "path": "src/main.py", "start_line": 5, "end_line": 8},
    )

    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["language"] == "python"
    assert result["total_lines"] == 20
    assert result["start_line"] == 5
    assert result["end_line"] == 8
    assert result["content"] == "line-5\nline-6\nline-7\nline-8"
    assert result["content_truncated"] is False


async def test_read_file_range_clamps_end_to_total_lines(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    content = "\n".join(f"line-{i}" for i in range(1, 6))
    _, slug = await _seed_repo_with_file(db_session, content=content)

    response = await _call_tool(
        client,
        plaintext,
        {
            "repository": slug,
            "path": "src/main.py",
            "start_line": 3,
            "end_line": 100,
        },
    )

    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["total_lines"] == 5
    assert result["end_line"] == 5
    assert result["content_truncated"] is True
    assert result["content"] == "line-3\nline-4\nline-5"


async def test_read_file_range_rejects_oversized_window(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    _, slug = await _seed_repo_with_file(db_session, content="x")

    response = await _call_tool(
        client,
        plaintext,
        {
            "repository": slug,
            "path": "src/main.py",
            "start_line": 1,
            "end_line": 1500,
        },
    )

    payload = response.json()
    assert payload["result"]["isError"] is True
    assert "INVALID_RANGE" in payload["result"]["content"][0]["text"]


async def test_read_file_range_returns_not_found_for_unknown_path(client, db_session):
    _, plaintext = await _seed_pat_user(db_session)
    _, slug = await _seed_repo_with_file(db_session, content="hello")

    response = await _call_tool(
        client,
        plaintext,
        {
            "repository": slug,
            "path": "missing.py",
            "start_line": 1,
            "end_line": 5,
        },
    )

    payload = response.json()
    assert payload["result"]["isError"] is True
    assert "NOT_FOUND" in payload["result"]["content"][0]["text"]
