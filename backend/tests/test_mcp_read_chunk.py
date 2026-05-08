"""MCP `cograph.read_chunk` smoke — happy path, cross-collection guard."""

from __future__ import annotations

import hashlib
import json

from backend.app.models.enums import UserRole
from backend.app.models.md_collection import MdChunk, MdCollection, MdDocument
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.user import User


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _seed_pat_user(db_session) -> tuple[User, str]:
    user = User(
        email="rc-tool@example.com",
        password_hash="x",
        name="Chunk",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "c" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="rc-tool",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "mcp"],
        )
    )
    await db_session.commit()
    return user, plaintext


async def _seed_chunk(db_session, *, owner_id, content: str) -> tuple[MdCollection, MdDocument, MdChunk]:
    collection = MdCollection(
        name="notes",
        description=None,
        owner_id=owner_id,
        visibility="private",
    )
    db_session.add(collection)
    await db_session.flush()
    document = MdDocument(
        collection_id=collection.id,
        source_key="guide.md",
        title="Guide",
        content=content,
        content_hash="doc",
        bytes=len(content),
        word_count=len(content.split()),
        line_count=content.count("\n") + 1,
        frontmatter={},
        heading_tree=[],
        code_blocks=[],
        tables=[],
        links=[],
    )
    db_session.add(document)
    await db_session.flush()
    chunk = MdChunk(
        document_id=document.id,
        chunk_index=0,
        heading_path=["Guide"],
        content=content,
    )
    db_session.add(chunk)
    await db_session.commit()
    return collection, document, chunk


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
                "name": "cograph.read_chunk",
                "arguments": args,
            },
        },
    )


async def test_read_chunk_returns_full_content(client, db_session):
    user, plaintext = await _seed_pat_user(db_session)
    collection, _, chunk = await _seed_chunk(
        db_session,
        owner_id=user.id,
        content="Long markdown chunk content that should be returned verbatim.",
    )

    response = await _call_tool(
        client,
        plaintext,
        {"collection_id": str(collection.id), "chunk_id": str(chunk.id)},
    )

    assert response.status_code == 200
    result = json.loads(response.json()["result"]["content"][0]["text"])
    assert result["chunk_id"] == str(chunk.id)
    assert result["content"].startswith("Long markdown chunk")
    assert result["source_key"] == "guide.md"
    assert result["heading_path"] == ["Guide"]


async def test_read_chunk_rejects_chunk_from_other_collection(client, db_session):
    user, plaintext = await _seed_pat_user(db_session)
    other_collection, _, other_chunk = await _seed_chunk(
        db_session,
        owner_id=user.id,
        content="In collection A.",
    )
    target_collection = MdCollection(
        name="target",
        description=None,
        owner_id=user.id,
        visibility="private",
    )
    db_session.add(target_collection)
    await db_session.commit()

    response = await _call_tool(
        client,
        plaintext,
        {
            "collection_id": str(target_collection.id),
            "chunk_id": str(other_chunk.id),
        },
    )

    payload = response.json()
    assert payload["result"]["isError"] is True
    assert "NOT_FOUND" in payload["result"]["content"][0]["text"]
