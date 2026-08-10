"""Unit tests for N-way Reciprocal Rank Fusion (Phase 7d).

The 2-way ``_rrf_merge`` in retriever.py only handles vector + BM25.  Phase 7d
needs a generic N-way merge to fuse {vector, lexical, symbol} streams (and to
keep the door open for future stores).  These tests pin the contract so the
HybridRetriever can rely on deterministic, capped, golden-stable output.
"""
from __future__ import annotations

import uuid

import pytest

from backend.app.rag.fusion import rrf_merge_streams  # type: ignore[import-not-found]
from backend.app.rag.retriever import RetrievedChunk


def _chunk(cid: uuid.UUID, score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(store="code", chunk_id=cid, content="x", score=score)


def test_rrf_3way_golden_scores():
    """3 streams, k=60. Expect score = sum(1 / (60 + rank_i)) over streams in which the chunk appears."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s1 = [_chunk(a), _chunk(b)]              # a:rank1, b:rank2
    s2 = [_chunk(b), _chunk(c)]              # b:rank1, c:rank2
    s3 = [_chunk(a), _chunk(c), _chunk(b)]   # a:rank1, c:rank2, b:rank3

    merged = rrf_merge_streams([s1, s2, s3], k=60)
    by_id = {c.chunk_id: c.score for c in merged}

    expected_a = 1 / 61 + 1 / 61
    expected_b = 1 / 62 + 1 / 61 + 1 / 63
    expected_c = 1 / 62 + 1 / 62
    assert by_id[a] == pytest.approx(expected_a)
    assert by_id[b] == pytest.approx(expected_b)
    assert by_id[c] == pytest.approx(expected_c)
    # b should rank highest because it appears in all 3 streams (highest cumulative score)
    assert merged[0].chunk_id == b


def test_rrf_n_way_generic_supports_arbitrary_stream_count():
    """Should accept 1, 2, 3, 4, 5+ streams without changing semantics."""
    cid = uuid.uuid4()
    streams = [[_chunk(cid)] for _ in range(5)]
    merged = rrf_merge_streams(streams, k=60)
    assert len(merged) == 1
    assert merged[0].score == pytest.approx(5 * (1 / 61))


def test_rrf_tiebreak_is_deterministic_by_chunk_id():
    """Two chunks with identical fused score must order by str(chunk_id) for determinism."""
    a = uuid.UUID("00000000-0000-0000-0000-000000000001")
    b = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    s1 = [_chunk(a)]
    s2 = [_chunk(b)]
    merged = rrf_merge_streams([s1, s2], k=60)
    # Both have score 1/61; tie-break by str(uuid) ascending.
    assert [c.chunk_id for c in merged] == [a, b]
    # Idempotent across calls (no hash-randomness leak).
    for _ in range(3):
        assert [c.chunk_id for c in rrf_merge_streams([s1, s2], k=60)] == [a, b]


def test_rrf_candidate_cap_truncates_each_stream_pre_merge():
    """candidate_cap should bound per-stream rank inflation before fusion."""
    ids = [uuid.uuid4() for _ in range(10)]
    s1 = [_chunk(cid) for cid in ids]
    s2 = [_chunk(cid) for cid in ids]
    merged = rrf_merge_streams([s1, s2], k=60, candidate_cap=3)
    # Only the first 3 of each stream should contribute — rank 4..10 truncated.
    contributing_ids = {c.chunk_id for c in merged}
    assert contributing_ids == set(ids[:3])


def test_rrf_empty_streams_returns_empty_list():
    assert rrf_merge_streams([], k=60) == []
    assert rrf_merge_streams([[], [], []], k=60) == []


def test_rrf_single_stream_preserves_input_order():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    merged = rrf_merge_streams([[_chunk(a), _chunk(b), _chunk(c)]], k=60)
    assert [m.chunk_id for m in merged] == [a, b, c]


def test_rrf_metadata_records_per_stream_rank():
    """Merged chunk's metadata should include the rank it had in each contributing stream."""
    a = uuid.uuid4()
    s1 = [_chunk(a)]                          # rank 1 in stream 0
    s2 = [_chunk(uuid.uuid4()), _chunk(a)]    # rank 2 in stream 1
    merged = rrf_merge_streams([s1, s2], k=60, stream_names=["vector", "lexical"])
    [hit] = [m for m in merged if m.chunk_id == a]
    assert hit.metadata.get("vector_rank") == 1
    assert hit.metadata.get("lexical_rank") == 2
