"""MCP `cograph_collection_document` — a long document is cut on a chunk
boundary and names the chunk to continue from."""

from __future__ import annotations

import hashlib
import json

from backend.app.md_rag.chunker import MdChunker
from backend.app.models.enums import UserRole
from backend.app.models.md_collection import MdChunk, MdCollection, MdDocument
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.user import User


async def _seed(db_session, content: str) -> tuple[str, MdCollection, MdDocument, list[MdChunk]]:
    user = User(
        email="doc-tool@example.com",
        password_hash="x",
        name="Doc",
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "cgr_pat_" + "d" * 48
    collection = MdCollection(
        name="warehouse", description=None, owner_id=user.id, visibility="private"
    )
    db_session.add_all(
        [
            PersonalAccessToken(
                user_id=user.id,
                name="doc-tool",
                token_hash=hashlib.sha256(plaintext.encode("utf-8")).digest(),
                token_prefix=plaintext[:16],
                scopes=["api:read", "mcp"],
            ),
            collection,
        ]
    )
    await db_session.flush()
    document = MdDocument(
        collection_id=collection.id,
        source_key="handbook.md",
        title="Handbook",
        content=content,
        content_hash="doc",
        bytes=len(content),
        frontmatter={},
        heading_tree=[{"level": 2, "text": "Bay 0", "anchor": "bay-0", "line": 1}],
        code_blocks=[],
        tables=[],
        links=[],
    )
    db_session.add(document)
    await db_session.flush()
    chunks = [
        MdChunk(
            document_id=document.id,
            chunk_index=draft.chunk_index,
            heading_path=draft.heading_path,
            content=draft.content,
        )
        for draft in MdChunker().chunk(content)
    ]
    db_session.add_all(chunks)
    await db_session.commit()
    return plaintext, collection, document, chunks


async def _call(client, plaintext: str, name: str, args: dict) -> dict:
    response = await client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {plaintext}", "Accept": "application/json"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    assert response.status_code == 200
    return json.loads(response.json()["result"]["content"][0]["text"])


async def test_long_document_is_cut_and_read_chunk_continues(client, db_session):
    # Ten 2700-char sections: the chunker keeps each one as its own chunk.
    sections = [
        f"## Bay {i}\n\n" + " ".join([f"Pallet row {i} holds crates."] * 100)
        for i in range(10)
    ]
    content = "\n\n".join(sections)
    plaintext, collection, _, chunks = await _seed(db_session, content)
    assert len(chunks) == 10

    doc = await _call(
        client,
        plaintext,
        "cograph_collection_document",
        {"collection_id": str(collection.id), "document_id": str(chunks[0].document_id)},
    )

    assert doc["content_truncated"] is True
    assert "heading_tree" not in doc and "code_blocks" not in doc
    cut = doc["next_chunk_index"]
    assert 0 < cut < 10
    assert doc["next_chunk_id"] == str(chunks[cut].id)
    assert doc["content"] == "\n\n".join(c.content for c in chunks[:cut])
    assert len(doc["content"]) <= 12000

    nxt = await _call(
        client,
        plaintext,
        "cograph_read_chunk",
        {"collection_id": str(collection.id), "chunk_id": doc["next_chunk_id"]},
    )
    # Nothing skipped, nothing repeated: the chunk is the text right after the cut.
    assert content.startswith(doc["content"] + "\n\n" + nxt["content"])
    assert nxt["next_chunk_id"] == str(chunks[cut + 1].id)

    last = await _call(
        client,
        plaintext,
        "cograph_read_chunk",
        {"collection_id": str(collection.id), "chunk_id": str(chunks[-1].id)},
    )
    assert last["next_chunk_id"] is None


async def test_oversized_first_chunk_is_cut_and_read_again_in_full(client, db_session):
    content = "```\n" + "crate 0042 bay 7\n" * 1500 + "```"
    plaintext, collection, document, chunks = await _seed(db_session, content)
    assert len(chunks) == 1 and len(chunks[0].content) > 12000

    doc = await _call(
        client,
        plaintext,
        "cograph_collection_document",
        {"collection_id": str(collection.id), "document_id": str(document.id)},
    )

    assert doc["content_truncated"] is True
    assert 0 < len(doc["content"]) <= 12000
    assert chunks[0].content.startswith(doc["content"])
    assert doc["next_chunk_id"] == str(chunks[0].id)


async def test_short_document_comes_back_whole(client, db_session):
    content = "## Bay 0\n\nShelves for small parcels."
    plaintext, collection, document, _ = await _seed(db_session, content)

    doc = await _call(
        client,
        plaintext,
        "cograph_collection_document",
        {"collection_id": str(collection.id), "document_id": str(document.id)},
    )

    assert doc["content"] == content
    assert doc["content_truncated"] is False
    assert doc["heading_tree"][0]["text"] == "Bay 0"
    assert "next_chunk_id" not in doc
