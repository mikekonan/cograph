"""Unit tests for BankDocumentEmbedderService skip/re-embed logic.

Covers:
- embeds all chunks when none have embeddings yet
- skips chunks whose content_hash + model both match
- re-embeds when model changes (even if content_hash is identical)
- re-embeds when chunk content changes (different content_hash)
- embed_documents scopes to given document IDs only
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.llm.bank_document_embedder import BankDocumentEmbedderService, _content_hash
from backend.app.models.bank import BankDocumentChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    *,
    chunk_id: uuid.UUID | None = None,
    content: str = "## Section\nSome bank text.",
    content_hash: str = "",
    embedding: list[float] | None = None,
    model: str = "",
    heading_path: list[str] | None = None,
) -> BankDocumentChunk:
    chunk = MagicMock(spec=BankDocumentChunk)
    chunk.id = chunk_id or uuid.uuid4()
    chunk.content = content
    chunk.content_hash = content_hash
    chunk.embedding = embedding
    chunk.model = model
    chunk.heading_path = heading_path or []
    return chunk


def _make_session(*, chunks: list[BankDocumentChunk]) -> AsyncMock:
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
# embed_bank tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embeds_all_chunks_when_none_embedded():
    """All chunks without embeddings are embedded on first run."""
    from backend.app.llm.embedder import FakeEmbedProvider

    chunks = [
        _make_chunk(content="# ADR-001\nInitial decision."),
        _make_chunk(content="## Context\nWhy we chose this approach."),
    ]
    session = _make_session(chunks=chunks)
    provider = FakeEmbedProvider(dims=8)
    service = BankDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_bank(session=session, bank_id=uuid.uuid4())

    assert result.embedded_nodes == 2
    assert result.skipped_nodes == 0
    assert result.model == "fake-embed-v1"
    # Two UPDATE statements issued (one per chunk), session committed
    assert session.execute.call_count == 2
    session.commit.assert_called()


@pytest.mark.asyncio
async def test_skips_unchanged():
    """Chunks whose content_hash + model match are skipped entirely."""
    from backend.app.llm.embedder import FakeEmbedProvider

    content = "# Stable ADR\nNo changes."
    chunk = _make_chunk(
        content=content,
        content_hash=_content_hash(content),
        embedding=[0.1] * 8,
        model="fake-embed-v1",
    )
    session = _make_session(chunks=[chunk])
    provider = FakeEmbedProvider(dims=8)
    service = BankDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_bank(session=session, bank_id=uuid.uuid4())

    assert result.skipped_nodes == 1
    assert result.embedded_nodes == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_reembeds_on_model_change():
    """When provider model differs from stored model, re-embed is forced."""
    from backend.app.llm.embedder import FakeEmbedProvider

    content = "# Section\nSame content."
    chunk = _make_chunk(
        content=content,
        content_hash=_content_hash(content),
        embedding=[0.1] * 8,
        model="old-model-v1",
    )
    session = _make_session(chunks=[chunk])
    provider = FakeEmbedProvider(dims=8)
    service = BankDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_bank(session=session, bank_id=uuid.uuid4())

    assert result.embedded_nodes == 1
    assert result.skipped_nodes == 0
    assert session.execute.call_count == 1


@pytest.mark.asyncio
async def test_reembeds_on_content_change():
    """Changed content_hash (stale stored hash) forces re-embed."""
    from backend.app.llm.embedder import FakeEmbedProvider

    old_content = "# Old ADR\nOriginal."
    new_content = "# New ADR\nRevised and extended."
    chunk = _make_chunk(
        content=new_content,
        content_hash=_content_hash(old_content),  # stale hash
        embedding=[0.1] * 8,
        model="fake-embed-v1",
    )
    session = _make_session(chunks=[chunk])
    provider = FakeEmbedProvider(dims=8)
    service = BankDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_bank(session=session, bank_id=uuid.uuid4())

    assert result.embedded_nodes == 1
    assert result.skipped_nodes == 0
    assert session.execute.call_count == 1


@pytest.mark.asyncio
async def test_empty_bank_returns_zero_result():
    """No chunks → EmbedResult with zeros, no DB writes."""
    from backend.app.llm.embedder import FakeEmbedProvider

    session = _make_session(chunks=[])
    provider = FakeEmbedProvider(dims=8)
    service = BankDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_bank(session=session, bank_id=uuid.uuid4())

    assert result.embedded_nodes == 0
    assert result.skipped_nodes == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# embed_documents tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_documents_scopes_to_ids():
    """embed_documents only processes chunks returned by the ID-filtered query."""
    from backend.app.llm.embedder import FakeEmbedProvider

    doc_a_id = uuid.uuid4()

    chunk_a1 = _make_chunk(content="# ADR A chunk 1")
    chunk_a2 = _make_chunk(content="# ADR A chunk 2")

    # Session returns only doc_a's chunks (simulates WHERE document_id IN [...] filter)
    session = _make_session(chunks=[chunk_a1, chunk_a2])
    provider = FakeEmbedProvider(dims=8)
    service = BankDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_documents(session=session, document_ids=[doc_a_id])

    assert result.embedded_nodes == 2
    assert result.skipped_nodes == 0
    assert session.execute.call_count == 2
    session.commit.assert_called()


@pytest.mark.asyncio
async def test_embed_documents_empty_ids_returns_zero():
    """embed_documents with empty document_ids short-circuits without touching DB."""
    from backend.app.llm.embedder import FakeEmbedProvider

    session = _make_session(chunks=[])
    provider = FakeEmbedProvider(dims=8)
    service = BankDocumentEmbedderService(provider, batch_size=256)

    result = await service.embed_documents(session=session, document_ids=[])

    assert result.embedded_nodes == 0
    assert result.skipped_nodes == 0
    session.commit.assert_not_called()
    session.execute.assert_not_called()
