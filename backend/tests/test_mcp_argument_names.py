"""Argument names on the MCP wire: one name per argument, two aliases, errors for the rest.

The SDK validates arguments against a model that ignores unknown keys, so a
misnamed argument used to vanish: `limit=3` on `cograph_search_code` ran with
the default `top_k`. These go through `/mcp/` so the server-level hook runs.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from backend.app.models.enums import (
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repository import Repository
from backend.app.models.user import User

_SLUG = "github.com/acme/warehouse"


async def _seed(db_session) -> str:
    user = User(
        email="arg-names@example.com",
        password_hash="x",
        name="Args",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "a" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="arg-names",
            token_hash=hashlib.sha256(plaintext.encode("utf-8")).digest(),
            token_prefix=plaintext[:16],
            scopes=["api:read", "mcp"],
        )
    )
    db_session.add(
        Repository(
            host="github.com",
            git_url="git@github.com:acme/warehouse.git",
            name="warehouse",
            owner="acme",
            branch="main",
            status=RepositoryStatus.READY,
            visibility=RepositoryVisibility.PUBLIC,
            sync_schedule=SyncSchedule.MANUAL,
        )
    )
    await db_session.commit()
    return plaintext


async def _search_code(client, plaintext: str, arguments: dict) -> dict:
    response = await client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {plaintext}", "Accept": "application/json"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "cograph_search_code", "arguments": arguments},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


async def test_limit_is_taken_as_top_k(client, db_session):
    plaintext = await _seed(db_session)
    seen: dict = {}

    async def _payload(**kwargs):
        seen.update(kwargs)
        return {"results": []}

    with patch("backend.app.mcp.tools.search_code.search_code_payload", _payload):
        result = await _search_code(
            client, plaintext, {"repository": _SLUG, "query": "Pallet", "limit": 3}
        )

    assert not result.get("isError"), result
    assert seen["top_k"] == 3


async def test_unknown_argument_is_an_error_naming_the_valid_ones(client, db_session):
    plaintext = await _seed(db_session)

    result = await _search_code(
        client, plaintext, {"repository": _SLUG, "query": "Pallet", "folder": "src/"}
    )

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert text.startswith("INVALID_REQUEST:"), text
    assert "folder" in text, text
    assert "repository, query, top_k" in text, text


async def test_alias_and_real_name_together_is_an_error(client, db_session):
    plaintext = await _seed(db_session)

    result = await _search_code(
        client,
        plaintext,
        {"repository": _SLUG, "query": "Pallet", "top_k": 5, "limit": 3},
    )

    assert result["isError"] is True
    assert "INVALID_REQUEST" in result["content"][0]["text"]
