"""Integration tests for md-collection search and embed status (Phase 7d+).

These tests require a live PostgreSQL with pgvector because they exercise:
- vector similarity search via ``embedding <=> CAST(:qvec AS vector)``
- BM25 lexical search via ``ts_rank_cd`` + ``plainto_tsquery``
- RRF fusion of both streams
"""

from __future__ import annotations

import pytest

from backend.app.api.md_collections import get_md_search_embed_provider
from backend.app.models.enums import MdCollectionVisibility, UserRole
from backend.app.models.md_collection import MdChunk, MdCollection, MdDocument
from backend.app.models.user import User


class _StubEmbedProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


def _auth_client(client, settings, user_id: str) -> None:
    from backend.app.core.auth import TokenType, create_token

    token = create_token(
        user_id=user_id,
        role=UserRole.USER,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf="csrf-token",
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.headers["X-CSRF-Token"] = "csrf-token"


@pytest.mark.asyncio
async def test_search_md_collection_hybrid_retrieval(
    integration_app,
    integration_client,
    integration_session_manager,
    integration_settings,
):
    async with integration_session_manager.session() as session:
        owner = User(
            email="owner@example.com",
            password_hash="hashed",
            role=UserRole.USER,
        )
        session.add(owner)
        await session.flush()

        collection = MdCollection(
            name="test-col",
            description="",
            visibility=MdCollectionVisibility.PRIVATE,
            owner_id=owner.id,
        )
        session.add(collection)
        await session.flush()

        document = MdDocument(
            collection_id=collection.id,
            source_key="guide.md",
            title="Guide",
            content="how retries are handled in the system",
            content_hash="abc",
            bytes=40,
            word_count=7,
            line_count=1,
        )
        session.add(document)
        await session.flush()

        chunk = MdChunk(
            document_id=document.id,
            chunk_index=0,
            heading_path=["Guide"],
            content="how retries are handled in the system",
            content_hash="abc",
            embedding=[0.1] * 1536,
            model="text-embedding-3-small",
        )
        session.add(chunk)
        await session.commit()

    _auth_client(integration_client, integration_settings, str(owner.id))

    integration_app.dependency_overrides[get_md_search_embed_provider] = (
        lambda: _StubEmbedProvider()
    )
    try:
        response = await integration_client.post(
            f"/api/md-collections/{collection.id}/search",
            json={"query": "retries", "top_k": 5},
        )
    finally:
        integration_app.dependency_overrides.pop(get_md_search_embed_provider, None)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["content"] == "how retries are handled in the system"
    assert result["source_key"] == "guide.md"
    # Both vector and lexical should match because the query keyword is in the content
    # and the stub embed provider returns the same vector as the chunk embedding.
    assert result["lexical_rank"] is not None
    assert result["score"] > 0


@pytest.mark.asyncio
async def test_search_md_collection_returns_empty_when_no_match(
    integration_app,
    integration_client,
    integration_session_manager,
    integration_settings,
):
    async with integration_session_manager.session() as session:
        owner = User(
            email="owner2@example.com",
            password_hash="hashed",
            role=UserRole.USER,
        )
        session.add(owner)
        await session.flush()

        collection = MdCollection(
            name="empty-col",
            description="",
            visibility=MdCollectionVisibility.PRIVATE,
            owner_id=owner.id,
        )
        session.add(collection)
        await session.flush()

        # No chunks in this collection — search should return empty regardless of query
        await session.commit()

    _auth_client(integration_client, integration_settings, str(owner.id))

    integration_app.dependency_overrides[get_md_search_embed_provider] = (
        lambda: _StubEmbedProvider()
    )
    try:
        response = await integration_client.post(
            f"/api/md-collections/{collection.id}/search",
            json={"query": "nonexistent-xyz-123", "top_k": 5},
        )
    finally:
        integration_app.dependency_overrides.pop(get_md_search_embed_provider, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"] == []


@pytest.mark.asyncio
async def test_embed_status_counts_correctly(
    integration_client,
    integration_session_manager,
    integration_settings,
):
    async with integration_session_manager.session() as session:
        owner = User(
            email="owner3@example.com",
            password_hash="hashed",
            role=UserRole.USER,
        )
        session.add(owner)
        await session.flush()

        collection = MdCollection(
            name="status-col",
            description="",
            visibility=MdCollectionVisibility.PRIVATE,
            owner_id=owner.id,
        )
        session.add(collection)
        await session.flush()

        document = MdDocument(
            collection_id=collection.id,
            source_key="doc.md",
            title="Doc",
            content="hello world",
            content_hash="abc",
            bytes=11,
            word_count=2,
            line_count=1,
        )
        session.add(document)
        await session.flush()

        chunk_with_embed = MdChunk(
            document_id=document.id,
            chunk_index=0,
            heading_path=[],
            content="hello world",
            content_hash="abc",
            embedding=[0.1] * 1536,
            model="text-embedding-3-small",
        )
        chunk_without_embed = MdChunk(
            document_id=document.id,
            chunk_index=1,
            heading_path=[],
            content="goodbye world",
            content_hash="def",
            embedding=None,
            model="",
        )
        session.add_all([chunk_with_embed, chunk_without_embed])
        await session.commit()

    _auth_client(integration_client, integration_settings, str(owner.id))

    response = await integration_client.get(
        f"/api/md-collections/{collection.id}/embed-status"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_chunks"] == 2
    assert payload["embedded_chunks"] == 1
    assert payload["is_ready"] is False

    # Now embed the second chunk
    async with integration_session_manager.session() as session:
        chunk = await session.get(MdChunk, chunk_without_embed.id)
        chunk.embedding = [0.2] * 1536
        chunk.model = "text-embedding-3-small"
        await session.commit()

    response = await integration_client.get(
        f"/api/md-collections/{collection.id}/embed-status"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_chunks"] == 2
    assert payload["embedded_chunks"] == 2
    assert payload["is_ready"] is True
