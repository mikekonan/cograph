"""Tests for MdChunkEmbedderService."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import select

from backend.app.llm.md_chunk_embedder import MdChunkEmbedderService, _content_hash
from backend.app.models.enums import MdCollectionVisibility
from backend.app.models.md_collection import MdChunk, MdCollection, MdDocument


class _FakeProvider:
    def __init__(
        self,
        model: str = "fake-model",
        in_transaction: Callable[[], bool] | None = None,
    ):
        self.model = model
        self.dimensions = 1536
        self.calls: list[list[str]] = []
        self.in_transaction = in_transaction
        self.in_transaction_during_calls: list[bool] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.in_transaction is not None:
            self.in_transaction_during_calls.append(self.in_transaction())
        self.calls.append(texts)
        return [[0.1] * 1536 for _ in texts]


async def _make_collection(db_session):
    col = MdCollection(
        name="test-col",
        description="",
        visibility=MdCollectionVisibility.PRIVATE,
        owner_id=None,
    )
    db_session.add(col)
    await db_session.flush()
    return col


@pytest.mark.asyncio
async def test_replace_chunks_sets_content_hash(db_session):
    """Bug fix: _replace_chunks must set content_hash so the embedder skip logic works."""
    from backend.app.md_rag.indexer import MdIndexer

    collection = await _make_collection(db_session)
    indexer = MdIndexer()
    doc = MdDocument(
        collection_id=collection.id,
        source_key="test.md",
        title="Test",
        content="hello world",
        content_hash="abc123",
        bytes=11,
        word_count=2,
        line_count=1,
    )
    db_session.add(doc)
    await db_session.flush()

    count = await indexer._replace_chunks(
        session=db_session,
        document=doc,
        content="hello world",
    )
    assert count > 0
    await db_session.commit()

    chunks = list(
        (
            await db_session.scalars(
                select(MdChunk).where(MdChunk.document_id == doc.id)
            )
        ).all()
    )
    assert len(chunks) == count
    for chunk in chunks:
        assert chunk.content_hash != ""
        assert chunk.content_hash == _content_hash(chunk.content)


@pytest.mark.asyncio
async def test_embed_chunks_skips_unchanged_chunks(db_session):
    """Chunks with matching content_hash + model + embedding should be skipped."""
    collection = await _make_collection(db_session)
    doc = MdDocument(
        collection_id=collection.id,
        source_key="test.md",
        title="Test",
        content="hello world",
        content_hash="abc123",
        bytes=11,
        word_count=2,
        line_count=1,
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = MdChunk(
        document_id=doc.id,
        chunk_index=0,
        heading_path=[],
        content="hello world",
        content_hash=_content_hash("hello world"),
        embedding=[0.1] * 1536,
        model="fake-model",
    )
    db_session.add(chunk)
    await db_session.commit()

    provider = _FakeProvider(model="fake-model")
    embedder = MdChunkEmbedderService(provider)
    result = await embedder.embed_documents(
        session=db_session,
        document_ids=[doc.id],
    )

    assert result.embedded_nodes == 0
    assert result.skipped_nodes == 1
    assert len(provider.calls) == 0  # no API call


@pytest.mark.asyncio
async def test_embed_chunks_re_embeds_when_hash_mismatches(db_session):
    """Chunks with wrong content_hash should be re-embedded."""
    collection = await _make_collection(db_session)
    doc = MdDocument(
        collection_id=collection.id,
        source_key="test.md",
        title="Test",
        content="hello world",
        content_hash="abc123",
        bytes=11,
        word_count=2,
        line_count=1,
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = MdChunk(
        document_id=doc.id,
        chunk_index=0,
        heading_path=[],
        content="hello world",
        content_hash="wrong-hash",
        embedding=[0.1] * 1536,
        model="fake-model",
    )
    db_session.add(chunk)
    await db_session.commit()

    provider = _FakeProvider(model="fake-model")
    embedder = MdChunkEmbedderService(provider)
    result = await embedder.embed_documents(
        session=db_session,
        document_ids=[doc.id],
    )

    assert result.embedded_nodes == 1
    assert result.skipped_nodes == 0
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_embed_chunks_releases_session_before_embedding_batch(db_session):
    collection = await _make_collection(db_session)
    doc = MdDocument(
        collection_id=collection.id,
        source_key="test.md",
        title="Test",
        content="hello world",
        content_hash="abc123",
        bytes=11,
        word_count=2,
        line_count=1,
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = MdChunk(
        document_id=doc.id,
        chunk_index=0,
        heading_path=[],
        content="hello world",
        content_hash="wrong-hash",
        embedding=[0.1] * 1536,
        model="fake-model",
    )
    db_session.add(chunk)
    await db_session.commit()

    provider = _FakeProvider(
        model="fake-model",
        in_transaction=db_session.in_transaction,
    )
    embedder = MdChunkEmbedderService(provider)
    result = await embedder.embed_documents(
        session=db_session,
        document_ids=[doc.id],
    )

    assert result.embedded_nodes == 1
    assert provider.in_transaction_during_calls == [False]


@pytest.mark.asyncio
async def test_embed_chunks_handles_deleted_chunks_gracefully(db_session):
    """Bug fix: if chunks are deleted between read and write, don't crash or overcount."""
    collection = await _make_collection(db_session)
    doc = MdDocument(
        collection_id=collection.id,
        source_key="test.md",
        title="Test",
        content="chunk one\n\nchunk two",
        content_hash="abc123",
        bytes=22,
        word_count=4,
        line_count=3,
    )
    db_session.add(doc)
    await db_session.flush()

    chunk1 = MdChunk(
        document_id=doc.id,
        chunk_index=0,
        heading_path=[],
        content="chunk one",
        content_hash="",
        embedding=None,
        model="",
    )
    chunk2 = MdChunk(
        document_id=doc.id,
        chunk_index=1,
        heading_path=[],
        content="chunk two",
        content_hash="",
        embedding=None,
        model="",
    )
    db_session.add_all([chunk1, chunk2])
    await db_session.commit()

    # Re-read chunks to get IDs
    chunks = list(
        (
            await db_session.scalars(
                select(MdChunk).where(MdChunk.document_id == doc.id)
            )
        ).all()
    )
    assert len(chunks) == 2

    provider = _FakeProvider(model="fake-model")
    embedder = MdChunkEmbedderService(provider, batch_size=10)

    # Delete one chunk mid-flight by monkey-patching commit
    original_commit = db_session.commit
    commit_count = 0

    async def _patched_commit():
        nonlocal commit_count
        commit_count += 1
        await original_commit()
        # After the first commit (Phase 2), delete chunk2
        if commit_count == 1:
            await db_session.delete(chunk2)
            await original_commit()

    db_session.commit = _patched_commit

    try:
        result = await embedder.embed_documents(
            session=db_session,
            document_ids=[doc.id],
        )
    finally:
        db_session.commit = original_commit

    # Only chunk1 was embedded; chunk2 was deleted before Phase 3
    assert result.embedded_nodes == 1
    assert result.skipped_nodes == 0
