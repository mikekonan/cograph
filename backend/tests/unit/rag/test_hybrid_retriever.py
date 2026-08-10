"""Unit tests for HybridRetriever (Phase 7d).

HybridRetriever fans out to {vector, lexical, symbol} per active store, applies
a per-stream candidate cap, fuses via N-way RRF, and optionally invokes a
reranker.  These tests use stubs for each component so we can pin orchestration
behaviour without touching SQL.
"""
from __future__ import annotations

from datetime import UTC, datetime
import uuid
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.app.rag.hybrid import HybridRetriever  # type: ignore[import-not-found]
from backend.app.rag.retriever import RetrievedChunk


def _chunk(cid: uuid.UUID, store: str = "code", score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        store=store,
        chunk_id=cid,
        content="content",
        score=score,
        metadata={"qualified_name": f"pkg.x_{cid.hex[:6]}"},
    )


class _StubVector:
    def __init__(self, hits_by_store: dict[str, list[RetrievedChunk]]):
        self.hits = hits_by_store
        self.calls: list[str] = []

    async def search(self, _session, *, store, **_kw) -> list[RetrievedChunk]:
        self.calls.append(store)
        return list(self.hits.get(store, []))


class _StubLexical(_StubVector):
    pass


class _StubSymbol:
    def __init__(self, hits: list[RetrievedChunk]):
        self.hits = hits
        self.called = False

    async def search(self, _session, **_kw) -> list[RetrievedChunk]:
        self.called = True
        return list(self.hits)


class _CountingReranker:
    def __init__(self):
        self.calls = 0

    async def rerank(self, _query, candidates, top_k):
        self.calls += 1
        # Reverse to make the effect visible
        out = list(reversed(candidates))[:top_k]
        return [replace(c, metadata={**c.metadata, "rerank_score": float(i)}) for i, c in enumerate(out)]


class _NeverRouter:
    def should_rerank(self, _query, _candidates) -> bool:
        return False


class _AlwaysRouter:
    def should_rerank(self, _query, _candidates) -> bool:
        return True


@pytest.mark.asyncio
async def test_fan_out_invokes_all_three_streams_for_code_store():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    vector = _StubVector({"code": [_chunk(a)]})
    lexical = _StubLexical({"code": [_chunk(b)]})
    symbol = _StubSymbol([_chunk(c)])
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=_CountingReranker(),
        router=_NeverRouter(),
    )

    result = await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        repository_id=uuid.uuid4(),
        stores={"code"},
        top_k=10,
    )

    assert vector.calls == ["code"]
    assert lexical.calls == ["code"]
    assert symbol.called is True
    chunk_ids = {c.chunk_id for c in result}
    assert chunk_ids == {a, b, c}


@pytest.mark.asyncio
async def test_partial_failure_in_one_stream_degrades_gracefully():
    """A failed stream logs and is skipped — remaining streams must still merge."""
    a, b = uuid.uuid4(), uuid.uuid4()

    class _BoomVector:
        calls: list[Any] = []

        async def search(self, *_a, **_k):
            raise RuntimeError("vector down")

    lexical = _StubLexical({"code": [_chunk(a)]})
    symbol = _StubSymbol([_chunk(b)])
    retriever = HybridRetriever(
        vector=_BoomVector(),
        lexical=lexical,
        symbol=symbol,
        reranker=_CountingReranker(),
        router=_NeverRouter(),
    )

    result = await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        repository_id=uuid.uuid4(),
        stores={"code"},
        top_k=10,
    )
    chunk_ids = {c.chunk_id for c in result}
    assert chunk_ids == {a, b}


@pytest.mark.asyncio
async def test_per_stream_candidate_cap_applied_pre_merge():
    """Each stream truncated to candidate_cap before fusion to bound cost."""
    ids = [uuid.uuid4() for _ in range(20)]
    vector = _StubVector({"code": [_chunk(cid) for cid in ids]})
    lexical = _StubLexical({"code": []})
    symbol = _StubSymbol([])
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=_CountingReranker(),
        router=_NeverRouter(),
        candidate_cap=5,
    )

    result = await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        repository_id=uuid.uuid4(),
        stores={"code"},
        top_k=20,
    )
    # Only first 5 vector candidates should propagate.
    assert {c.chunk_id for c in result} == set(ids[:5])


@pytest.mark.asyncio
async def test_skips_code_store_without_repository_id():
    vector = _StubVector({"code": [_chunk(uuid.uuid4())]})
    lexical = _StubLexical({"code": []})
    symbol = _StubSymbol([])
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=_CountingReranker(),
        router=_NeverRouter(),
    )
    result = await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        repository_id=None,
        stores={"code"},
        top_k=10,
    )
    assert result == []
    assert vector.calls == []  # no fan-out without a repo to scope by


@pytest.mark.asyncio
async def test_invokes_reranker_when_router_allows():
    a, b = uuid.uuid4(), uuid.uuid4()
    vector = _StubVector({"code": [_chunk(a), _chunk(b)]})
    lexical = _StubLexical({"code": []})
    symbol = _StubSymbol([])
    reranker = _CountingReranker()
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=reranker,
        router=_AlwaysRouter(),
    )

    result = await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        repository_id=uuid.uuid4(),
        stores={"code"},
        top_k=10,
    )
    assert reranker.calls == 1
    # Reranker reverses → b first
    assert result[0].chunk_id == b
    assert "rerank_score" in result[0].metadata


@pytest.mark.asyncio
async def test_router_skip_means_reranker_never_called():
    a = uuid.uuid4()
    vector = _StubVector({"code": [_chunk(a)]})
    lexical = _StubLexical({"code": []})
    symbol = _StubSymbol([])
    reranker = _CountingReranker()
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=reranker,
        router=_NeverRouter(),
    )
    await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        repository_id=uuid.uuid4(),
        stores={"code"},
        top_k=10,
    )
    assert reranker.calls == 0


@pytest.mark.asyncio
async def test_empty_query_text_skips_lexical_and_symbol_streams():
    """Vector-only mode when there's no query text — keeps semantic recall path open."""
    a = uuid.uuid4()
    vector = _StubVector({"code": [_chunk(a)]})
    lexical = _StubLexical({"code": [_chunk(uuid.uuid4())]})
    symbol = _StubSymbol([_chunk(uuid.uuid4())])
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=_CountingReranker(),
        router=_NeverRouter(),
    )
    result = await retriever.retrieve(
        AsyncMock(),
        query_text="",
        query_embedding=[0.1] * 1536,
        repository_id=uuid.uuid4(),
        stores={"code"},
        top_k=10,
    )
    assert vector.calls == ["code"]
    assert lexical.calls == []
    assert symbol.called is False
    assert {c.chunk_id for c in result} == {a}


@pytest.mark.asyncio
async def test_md_collections_store_fans_out_vector_and_lexical():
    """Md-collections uses vector + lexical (no symbol lookup)."""
    a, b = uuid.uuid4(), uuid.uuid4()
    vector = _StubVector({"md_collections": [_chunk(a, store="md_collections")]})
    lexical = _StubLexical({"md_collections": [_chunk(b, store="md_collections")]})
    symbol = _StubSymbol([])
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=_CountingReranker(),
        router=_NeverRouter(),
    )

    result = await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        collection_id=uuid.uuid4(),
        stores={"md_collections"},
        top_k=10,
    )

    assert vector.calls == ["md_collections"]
    assert lexical.calls == ["md_collections"]
    assert symbol.called is False
    chunk_ids = {c.chunk_id for c in result}
    assert chunk_ids == {a, b}


@pytest.mark.asyncio
async def test_skips_md_collections_without_collection_id():
    vector = _StubVector({"md_collections": [_chunk(uuid.uuid4(), store="md_collections")]})
    lexical = _StubLexical({"md_collections": []})
    symbol = _StubSymbol([])
    retriever = HybridRetriever(
        vector=vector,
        lexical=lexical,
        symbol=symbol,
        reranker=_CountingReranker(),
        router=_NeverRouter(),
    )
    result = await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        collection_id=None,
        stores={"md_collections"},
        top_k=10,
    )
    assert result == []
    assert vector.calls == []


@pytest.mark.asyncio
async def test_forwards_temporal_filters_to_all_streams():
    captured: dict[str, dict[str, object]] = {}

    class _CaptureVector:
        async def search(self, _session, **kwargs):
            captured["vector"] = kwargs
            return []

    class _CaptureLexical:
        async def search(self, _session, **kwargs):
            captured["lexical"] = kwargs
            return []

    class _CaptureSymbol:
        async def search(self, _session, **kwargs):
            captured["symbol"] = kwargs
            return []

    retriever = HybridRetriever(
        vector=_CaptureVector(),
        lexical=_CaptureLexical(),
        symbol=_CaptureSymbol(),
        reranker=_CountingReranker(),
        router=_NeverRouter(),
    )
    since = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    until = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)

    await retriever.retrieve(
        AsyncMock(),
        query_text="foo",
        query_embedding=[0.1] * 1536,
        repository_id=uuid.uuid4(),
        stores={"code"},
        top_k=10,
        since=since,
        until=until,
    )

    assert captured["vector"]["since"] == since
    assert captured["vector"]["until"] == until
    assert captured["lexical"]["since"] == since
    assert captured["lexical"]["until"] == until
    assert captured["symbol"]["since"] == since
    assert captured["symbol"]["until"] == until
