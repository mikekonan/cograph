"""An empty MCP answer says why, and only what is true.

These go through `/mcp/`, like the agent does.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest

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
_HIT = {"node_id": "n1", "name": "shipping.ShipmentRouter", "at": "shipping/router.go:1-9"}


async def _seed(db_session, status=RepositoryStatus.READY) -> str:
    user = User(email="empty@example.com", password_hash="x", name="Empty", role=UserRole.USER)
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "e" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="empty",
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
            status=status,
            visibility=RepositoryVisibility.PUBLIC,
            sync_schedule=SyncSchedule.MANUAL,
        )
    )
    await db_session.commit()
    return plaintext


async def _call(client, plaintext: str, tool: str, arguments: dict) -> dict:
    response = await client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {plaintext}", "Accept": "application/json"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


def _answer(result: dict) -> dict:
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def _returns(payload: dict):
    async def _payload(**kwargs):
        return dict(payload)

    return _payload


@pytest.mark.parametrize(
    ("status", "why"),
    [
        (RepositoryStatus.INDEXING, "its indexing has not finished yet"),
        (RepositoryStatus.ERROR, "its first index failed, nothing is indexed"),
    ],
)
async def test_retrieve_on_a_repository_not_indexed_says_so(client, db_session, status, why):
    plaintext = await _seed(db_session, status)

    result = await _call(
        client, plaintext, "cograph_retrieve", {"repository": _SLUG, "query": "parcel routing"}
    )

    assert result["isError"] is True
    # The SDK prefixes "Error executing tool cograph_retrieve: ".
    assert result["content"][0]["text"].endswith(
        f"REPO_NOT_READY: Repository is not ready: {why}"
    )


async def test_search_code_without_hits_names_the_words_and_the_folder(client, db_session):
    plaintext = await _seed(db_session)

    with patch(
        "backend.app.mcp.tools.search_code.search_code_payload", _returns({"results": []})
    ):
        answer = _answer(
            await _call(
                client,
                plaintext,
                "cograph_search_code",
                {
                    "repository": _SLUG,
                    "query": "where is ShipmentRouter built",
                    "path_prefix": "internal/shipping/",
                },
            )
        )

    assert answer == {
        "results": [],
        "reason": "No code under path_prefix 'internal/shipping/' matches any of the "
        "words: shipmentrouter, built.",
    }


async def test_search_code_without_hits_points_to_the_name_elsewhere(client, db_session):
    plaintext = await _seed(db_session)
    elsewhere = [{"repository": "github.com/acme/shipping", "in": "code"}]
    seen: dict = {}

    async def _payload(**kwargs):
        seen.update(kwargs)
        return {"results": [], "elsewhere": elsewhere}

    with patch("backend.app.mcp.tools.search_code.search_code_payload", _payload):
        answer = _answer(
            await _call(
                client,
                plaintext,
                "cograph_search_code",
                {"repository": _SLUG, "query": "OrderShipped"},
            )
        )

    assert answer == {
        "results": [],
        "elsewhere": elsewhere,
        "reason": "No code matches any of the words: ordershipped. "
        "The name is in other repositories: see `elsewhere`.",
    }
    # The lookup is scoped to what the caller can read.
    assert seen["current_user"].email == "empty@example.com"


async def test_retrieve_without_hits_offers_a_call_only_for_a_code_name(client, db_session):
    plaintext = await _seed(db_session)

    async def _retrieve(query: str) -> dict:
        with patch("backend.app.mcp.tools.retrieve.retrieve_payload", _returns({"results": []})):
            return _answer(
                await _call(
                    client, plaintext, "cograph_retrieve", {"repository": _SLUG, "query": query}
                )
            )

    assert await _retrieve("where is ShipmentRouter built") == {
        "results": [],
        "next": "cograph_search_code(repository='github.com/acme/warehouse', "
        "query='ShipmentRouter')",
    }
    # No name from the code: nothing to suggest, and no advice to rephrase.
    assert await _retrieve("how are parcels routed") == {"results": []}


async def test_an_answer_with_hits_carries_no_reason_or_next(client, db_session):
    plaintext = await _seed(db_session)
    arguments = {"repository": _SLUG, "query": "where is ShipmentRouter built"}

    with (
        patch("backend.app.mcp.tools.retrieve.retrieve_payload", _returns({"results": [_HIT]})),
        patch(
            "backend.app.mcp.tools.search_code.search_code_payload",
            _returns({"results": [_HIT]}),
        ),
    ):
        for tool in ("cograph_retrieve", "cograph_search_code"):
            answer = _answer(await _call(client, plaintext, tool, arguments))
            assert answer == {"results": [_HIT]}, tool
