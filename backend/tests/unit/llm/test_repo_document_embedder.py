"""Unit tests for RepoDocumentEmbedderService skip/re-embed logic.

Covers:
- embeds all chunks when none have embeddings yet
- skips chunks whose content_hash + model both match
- re-embeds when model changes (even if content_hash is identical)
- re-embeds when chunk content changes (different content_hash)
"""
from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService, _content_hash
from backend.app.models.repo_document import RepoDocumentChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    *,
    chunk_id: uuid.UUID | None = None,
    content: str = "## Section\nSome text.",
    content_hash: str = "",
    embedding: list[float] | None = None,
    model: str = "",
    heading_path: list[str] | None = None,
) -> RepoDocumentChunk:
    chunk = MagicMock(spec=RepoDocumentChunk)
    chunk.id = chunk_id or uuid.uuid4()
    chunk.content = content
    chunk.content_hash = content_hash
    chunk.embedding = embedding
    chunk.model = model
    chunk.heading_path = heading_path or []
    return chunk


def _make_session(*, chunks: list[RepoDocumentChunk]) -> AsyncMock:
    session = AsyncMock()

    async def _scalars(stmt):
        result = MagicMock()
        result.all.return_value = chunks
        return result

    session.scalars = _scalars
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embeds_all_chunks_when_none_embedded():
    """All chunks without embeddings are embedded on first run."""
    chunks = [_make_chunk(content="# Doc\nHello world."), _make_chunk(content="## API\nSee above.")]
    session = _make_session(chunks=chunks)
    provider = FakeEmbedProvider(dims=8)
    service = RepoDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_repository(session=session, repository_id=uuid.uuid4())

    assert result.embedded_nodes == 2
    assert result.skipped_nodes == 0
    assert result.model == "fake-embed-v1"
    assert session.execute.call_count == 2
    session.commit.assert_called()


@pytest.mark.asyncio
async def test_skips_unchanged():
    """Chunks whose content_hash + model match are skipped entirely."""
    content = "# Stable\nNo changes here."
    computed = _content_hash(content)
    chunk = _make_chunk(
        content=content,
        content_hash=computed,
        embedding=[0.1] * 8,
        model="fake-embed-v1",
    )
    session = _make_session(chunks=[chunk])
    provider = FakeEmbedProvider(dims=8)
    service = RepoDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_repository(session=session, repository_id=uuid.uuid4())

    assert result.skipped_nodes == 1
    assert result.embedded_nodes == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_reembeds_on_model_change():
    """When the provider model differs from the stored model, re-embed is forced."""
    content = "# Section\nSame content."
    computed = _content_hash(content)
    chunk = _make_chunk(
        content=content,
        content_hash=computed,
        embedding=[0.1] * 8,
        model="old-model-v1",
    )
    session = _make_session(chunks=[chunk])
    provider = FakeEmbedProvider(dims=8)  # model = "fake-embed-v1"
    service = RepoDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_repository(session=session, repository_id=uuid.uuid4())

    assert result.embedded_nodes == 1
    assert result.skipped_nodes == 0
    assert session.execute.call_count == 1


@pytest.mark.asyncio
async def test_reembeds_on_content_change():
    """When chunk content changes (different hash), re-embed fires regardless of model."""
    old_content = "# Old\nOriginal text."
    new_content = "# New\nUpdated text with more detail."
    chunk = _make_chunk(
        content=new_content,
        content_hash=_content_hash(old_content),  # stale hash
        embedding=[0.1] * 8,
        model="fake-embed-v1",
    )
    session = _make_session(chunks=[chunk])
    provider = FakeEmbedProvider(dims=8)
    service = RepoDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_repository(session=session, repository_id=uuid.uuid4())

    assert result.embedded_nodes == 1
    assert result.skipped_nodes == 0
    assert session.execute.call_count == 1


@pytest.mark.asyncio
async def test_empty_repository_returns_zero_result():
    """No chunks → EmbedResult with zeros, no DB writes."""
    session = _make_session(chunks=[])
    provider = FakeEmbedProvider(dims=8)
    service = RepoDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_repository(session=session, repository_id=uuid.uuid4())

    assert result.embedded_nodes == 0
    assert result.skipped_nodes == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_content_hash_is_sha256_hex():
    content = "hello world"
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert _content_hash(content) == expected
    assert len(_content_hash(content)) == 64
