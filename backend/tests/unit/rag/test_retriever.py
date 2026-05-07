"""Unit tests for RagRetriever — hybrid kNN + BM25 via RRF across code / repo_docs."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.rag.retriever import RagRetriever, RetrievedChunk, _rrf_merge


def _make_row(**fields) -> dict:
    return fields


def _session_with_rows(rows_per_call: list[list[dict]]) -> AsyncMock:
    """Return an AsyncSession mock whose ``execute`` yields mapped rows.

    Each call to ``session.execute`` pops one batch from ``rows_per_call``.
    If exhausted, subsequent calls return [].
    """
    session = AsyncMock()
    call_index = {"i": 0}

    async def _execute(_stmt, _params=None):
        i = call_index["i"]
        call_index["i"] += 1
        rows = rows_per_call[i] if i < len(rows_per_call) else []
        result = MagicMock()
        mappings = MagicMock()
        mappings.all.return_value = rows
        result.mappings.return_value = mappings
        return result

    session.execute = _execute
    return session


# ---------------------------------------------------------------------------
# Existing tests — updated for hybrid signature and dual query paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_returns_empty_when_no_stores_active():
    retriever = RagRetriever()
    session = _session_with_rows([])

    result = await retriever.retrieve(
        session,
        "",
        query_embedding=[0.1] * 1536,
        stores=set(),
    )

    assert result == []


@pytest.mark.asyncio
async def test_retrieve_code_store_only_with_no_repo_id_returns_empty():
    """A code/repo_docs query needs repository_id; without it the store is skipped."""
    retriever = RagRetriever()
    session = _session_with_rows([])

    result = await retriever.retrieve(
        session,
        "foo",
        query_embedding=[0.1] * 1536,
        stores={"code"},
        repository_id=None,
    )

    assert result == []


@pytest.mark.asyncio
async def test_retrieve_from_code_store():
    retriever = RagRetriever()
    repo_id = uuid.uuid4()
    chunk_a = uuid.uuid4()
    chunk_b = uuid.uuid4()

    # chunk_a matches both vector (rank 1) and BM25 (rank 1); chunk_b vector only (rank 2)
    vector_rows = [
        _make_row(
            chunk_id=chunk_a,
            content="def foo(): ...",
            qualified_name="pkg.foo",
            file_path="pkg/a.py",
            language="python",
            start_line=1,
            end_line=3,
            score=0.91,
        ),
        _make_row(
            chunk_id=chunk_b,
            content="def bar(): ...",
            qualified_name="pkg.bar",
            file_path="pkg/b.py",
            language="python",
            start_line=4,
            end_line=6,
            score=0.72,
        ),
    ]
    bm25_rows = [
        _make_row(
            chunk_id=chunk_a,
            content="def foo(): ...",
            qualified_name="pkg.foo",
            file_path="pkg/a.py",
            language="python",
            start_line=1,
            end_line=3,
            score=0.75,
        ),
    ]
    # call 0 = _vector_code, call 1 = _bm25_code
    session = _session_with_rows([vector_rows, bm25_rows])

    result = await retriever.retrieve(
        session,
        "def foo",
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        stores={"code"},
        top_k=5,
    )

    assert len(result) == 2
    assert all(isinstance(c, RetrievedChunk) for c in result)
    assert {c.chunk_id for c in result} == {chunk_a, chunk_b}
    assert all(c.store == "code" for c in result)
    # chunk_a has higher RRF score: appeared in both lists (2*(1/61)) vs chunk_b (1/62)
    assert result[0].score > result[1].score
    assert result[0].chunk_id == chunk_a
    assert result[0].metadata["qualified_name"] == "pkg.foo"
    assert result[0].metadata["file_path"] == "pkg/a.py"
    assert result[0].metadata["vector_rank"] == 1
    assert result[0].metadata["bm25_rank"] == 1
    assert result[1].metadata["bm25_rank"] is None


@pytest.mark.asyncio
async def test_retrieve_merges_both_stores_and_respects_top_k():
    retriever = RagRetriever()
    repo_id = uuid.uuid4()

    code_chunk_a = uuid.uuid4()
    code_chunk_b = uuid.uuid4()
    repo_doc_chunk = uuid.uuid4()

    code_rows = [
        _make_row(
            chunk_id=code_chunk_a,
            content="code-high",
            qualified_name="pkg.high",
            file_path="a.py",
            language="python",
            start_line=1,
            end_line=2,
            score=0.95,
        ),
        _make_row(
            chunk_id=code_chunk_b,
            content="code-low",
            qualified_name="pkg.low",
            file_path="b.py",
            language="python",
            start_line=3,
            end_line=4,
            score=0.40,
        ),
    ]
    repo_doc_rows = [
        _make_row(
            chunk_id=repo_doc_chunk,
            content="doc-mid",
            chunk_index=0,
            heading_path=["Guide"],
            file_path="README.md",
            title="README",
            score=0.80,
        ),
    ]
    # 4 calls: vector_code, bm25_code, vector_repo_docs, bm25_repo_docs
    session = _session_with_rows([code_rows, [], repo_doc_rows, []])

    result = await retriever.retrieve(
        session,
        "hello",
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        top_k=2,
    )

    assert len(result) == 2
    scores = [c.score for c in result]
    assert scores == sorted(scores, reverse=True)
    ids_returned = {c.chunk_id for c in result}
    assert code_chunk_b not in ids_returned
    assert {code_chunk_a, repo_doc_chunk} == ids_returned
    assert any(c.store == "code" for c in result)
    assert any(c.store == "repo_docs" for c in result)


@pytest.mark.asyncio
async def test_retrieve_degrades_gracefully_when_one_store_raises():
    retriever = RagRetriever()
    repo_id = uuid.uuid4()

    good_code_rows = [
        _make_row(
            chunk_id=uuid.uuid4(),
            content="code-ok",
            qualified_name="pkg.ok",
            file_path="a.py",
            language="python",
            start_line=1,
            end_line=2,
            score=0.5,
        ),
    ]

    call_index = {"i": 0}

    async def _execute(_stmt, _params=None):
        i = call_index["i"]
        call_index["i"] += 1
        # call 0 = vector_code (ok), 1 = bm25_code (ok empty),
        # 2 = vector_repo_docs (raises), 3 = bm25_repo_docs (ok empty)
        if i == 0:
            result = MagicMock()
            result.mappings.return_value.all.return_value = good_code_rows
            return result
        if i == 1:
            result = MagicMock()
            result.mappings.return_value.all.return_value = []
            return result
        if i == 2:
            raise RuntimeError("pgvector index missing")
        result = MagicMock()
        result.mappings.return_value.all.return_value = []
        return result

    session = AsyncMock()
    session.execute = _execute

    result = await retriever.retrieve(
        session,
        "hello",
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        top_k=10,
    )

    assert len(result) == 1
    assert result[0].store == "code"


# ---------------------------------------------------------------------------
# New tests for hybrid-specific behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bm25_only_match():
    """Chunk with no vector match but a BM25 match is still returned."""
    retriever = RagRetriever()
    repo_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    bm25_row = _make_row(
        chunk_id=chunk_id,
        content="def authenticate(token): ...",
        qualified_name="auth.authenticate",
        file_path="auth.py",
        language="python",
        start_line=1,
        end_line=5,
        score=0.85,
    )
    # call 0 = vector_code (no matches), call 1 = bm25_code (one match)
    session = _session_with_rows([[], [bm25_row]])

    result = await retriever.retrieve(
        session,
        "authenticate",
        query_embedding=[0.0] * 1536,
        repository_id=repo_id,
        stores={"code"},
        top_k=5,
    )

    assert len(result) == 1
    assert result[0].chunk_id == chunk_id
    assert result[0].store == "code"
    assert result[0].metadata["bm25_rank"] == 1
    assert result[0].metadata["vector_rank"] is None


@pytest.mark.asyncio
async def test_rrf_overlap_boost():
    """A chunk in both vector and BM25 lists gets a higher RRF score than single-list chunks."""
    retriever = RagRetriever()
    repo_id = uuid.uuid4()
    overlap_id = uuid.uuid4()
    vector_only_id = uuid.uuid4()

    overlap_row = _make_row(
        chunk_id=overlap_id,
        content="def foo(): ...",
        qualified_name="pkg.foo",
        file_path="a.py",
        language="python",
        start_line=1,
        end_line=2,
        score=0.7,
    )
    vector_only_row = _make_row(
        chunk_id=vector_only_id,
        content="def bar(): ...",
        qualified_name="pkg.bar",
        file_path="b.py",
        language="python",
        start_line=3,
        end_line=4,
        score=0.9,  # higher raw cosine score, but only in one list
    )

    # vector: [vector_only(rank 1), overlap(rank 2)]
    # bm25:   [overlap(rank 1)]
    # RRF: overlap = 1/62 + 1/61 ≈ 0.0278 vs vector_only = 1/61 ≈ 0.0164
    session = _session_with_rows([[vector_only_row, overlap_row], [overlap_row]])

    result = await retriever.retrieve(
        session,
        "foo",
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        stores={"code"},
        top_k=5,
    )

    assert len(result) == 2
    assert result[0].chunk_id == overlap_id
    assert result[1].chunk_id == vector_only_id
    assert result[0].score > result[1].score
    assert result[0].metadata["vector_rank"] == 2
    assert result[0].metadata["bm25_rank"] == 1
    assert result[1].metadata["vector_rank"] == 1
    assert result[1].metadata["bm25_rank"] is None


@pytest.mark.asyncio
async def test_query_text_empty_skips_bm25():
    """Empty query_text disables BM25; only one vector execute call is made per store."""
    retriever = RagRetriever()
    repo_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    rows = [
        _make_row(
            chunk_id=chunk_id,
            content="def foo(): ...",
            qualified_name="pkg.foo",
            file_path="a.py",
            language="python",
            start_line=1,
            end_line=2,
            score=0.8,
        ),
    ]
    execute_count = {"n": 0}

    async def _counting_execute(_stmt, _params=None):
        execute_count["n"] += 1
        result = MagicMock()
        result.mappings.return_value.all.return_value = rows
        return result

    session = AsyncMock()
    session.execute = _counting_execute

    result = await retriever.retrieve(
        session,
        "",  # empty — BM25 disabled
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        stores={"code"},
        top_k=5,
    )

    assert len(result) == 1
    assert execute_count["n"] == 1  # only vector_code, no bm25_code
    assert result[0].chunk_id == chunk_id


# ---------------------------------------------------------------------------
# C3 / L1 / M1 / M3 / H3 — new robustness tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vector_failure_falls_back_to_bm25():
    """C3: vector query failure must not suppress the BM25 query for the same store."""
    retriever = RagRetriever()
    repo_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    bm25_row = _make_row(
        chunk_id=chunk_id,
        content="def search(): ...",
        chunk_index=0,
        heading_path=["API"],
        file_path="docs/api.md",
        title="API Docs",
        score=0.75,
    )

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("pgvector unavailable")

    retriever._vector_repo_docs = _raise  # type: ignore[method-assign]
    session = _session_with_rows([[bm25_row]])  # single execute call: bm25_repo_docs

    result = await retriever.retrieve(
        session,
        "search query",
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        stores={"repo_docs"},
        top_k=5,
    )

    assert len(result) == 1
    assert result[0].chunk_id == chunk_id
    assert result[0].store == "repo_docs"


@pytest.mark.asyncio
async def test_bm25_failure_falls_back_to_vector():
    """C3: BM25 query failure must not suppress vector results for the same store."""
    retriever = RagRetriever()
    repo_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    vector_row = _make_row(
        chunk_id=chunk_id,
        content="def process(): ...",
        qualified_name="pkg.process",
        file_path="a.py",
        language="python",
        start_line=1,
        end_line=3,
        score=0.88,
    )

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("BM25 index corrupt")

    retriever._bm25_code = _raise  # type: ignore[method-assign]
    session = _session_with_rows([[vector_row]])  # single execute call: vector_code

    result = await retriever.retrieve(
        session,
        "process",
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        stores={"code"},
        top_k=5,
    )

    assert len(result) == 1
    assert result[0].chunk_id == chunk_id
    assert result[0].store == "code"


@pytest.mark.asyncio
async def test_query_embedding_wrong_dim_raises():
    """M1: retrieve() must raise ValueError immediately when embedding dim != 1536."""
    retriever = RagRetriever()
    session = _session_with_rows([])

    with pytest.raises(ValueError, match="1536"):
        await retriever.retrieve(
            session,
            "query",
            query_embedding=[0.1] * 10,
            stores={"code"},
            repository_id=uuid.uuid4(),
        )


def test_rrf_tiebreaker_is_deterministic():
    """M3: two chunks with equal RRF score must produce identical ordering across calls."""
    id_a = uuid.UUID("00000000-0000-0000-0000-000000000001")
    id_b = uuid.UUID("00000000-0000-0000-0000-000000000002")

    chunk_a = RetrievedChunk(store="code", chunk_id=id_a, content="a", score=0.9, metadata={})
    chunk_b = RetrievedChunk(store="code", chunk_id=id_b, content="b", score=0.8, metadata={})

    # Both appear once at rank 1 in different lists → equal RRF score = 1/(60+1)
    results = [_rrf_merge([chunk_a], [chunk_b]) for _ in range(5)]
    orders = [tuple(c.chunk_id for c in r) for r in results]
    assert len(set(orders)) == 1, "ordering must be identical across all calls"
    # id_a < id_b as string → id_a wins the tiebreak (ascending secondary sort)
    assert results[0][0].chunk_id == id_a
    assert results[0][1].chunk_id == id_b


@pytest.mark.asyncio
async def test_vector_only_mode_preserves_cosine_ordering():
    """H3: empty query_text (vector-only) must preserve cosine score order, not RRF scores."""
    retriever = RagRetriever()
    repo_id = uuid.uuid4()
    chunk_high = uuid.uuid4()
    chunk_low = uuid.uuid4()

    vector_rows = [
        _make_row(
            chunk_id=chunk_high,
            content="high cosine",
            qualified_name="pkg.high",
            file_path="a.py",
            language="python",
            start_line=1,
            end_line=2,
            score=0.95,
        ),
        _make_row(
            chunk_id=chunk_low,
            content="low cosine",
            qualified_name="pkg.low",
            file_path="b.py",
            language="python",
            start_line=3,
            end_line=4,
            score=0.30,
        ),
    ]
    session = _session_with_rows([vector_rows])  # single execute: vector_code only

    result = await retriever.retrieve(
        session,
        "",  # empty → use_bm25=False, no RRF
        query_embedding=[0.1] * 1536,
        repository_id=repo_id,
        stores={"code"},
        top_k=5,
    )

    assert len(result) == 2
    assert result[0].chunk_id == chunk_high
    assert result[1].chunk_id == chunk_low
    assert result[0].score == pytest.approx(0.95)
    assert result[1].score == pytest.approx(0.30)
