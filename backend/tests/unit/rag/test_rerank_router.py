"""Unit tests for RerankRouter (Phase 7d).

The router decides whether the cross-encoder is worth invoking on a given
candidate set.  Skipping reranking when it can't help (exact symbol hit,
trivially small candidate set, provider disabled) keeps p95 latency bounded.
"""
from __future__ import annotations

import uuid

import pytest

from backend.app.rag.retriever import RetrievedChunk
from backend.app.rag.router import RerankRouter  # type: ignore[import-not-found]


def _chunk(qualified_name: str | None, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        store="code",
        chunk_id=uuid.uuid4(),
        content="x",
        score=score,
        metadata={"qualified_name": qualified_name} if qualified_name else {},
    )


def test_skip_when_exact_symbol_hit_in_top_n():
    """If a top-3 candidate's qualified_name (case-insensitive, last segment) equals
    the query, the router should treat it as an authoritative match — no rerank.
    """
    router = RerankRouter(rerank_threshold=10, exact_match_top_n=3)
    candidates = [
        _chunk("pkg.module.parseHttpRequest"),
        _chunk("pkg.other.helper"),
        _chunk("pkg.module.HttpClient"),
    ] + [_chunk("pkg.x.unrelated") for _ in range(20)]
    assert router.should_rerank("parseHttpRequest", candidates) is False


def test_invoke_for_semantic_query_without_exact_match():
    router = RerankRouter(rerank_threshold=10, exact_match_top_n=3)
    candidates = [_chunk("pkg.x.unrelated") for _ in range(20)]
    assert router.should_rerank("how are retries handled", candidates) is True


def test_skip_when_candidate_count_below_threshold():
    """Tiny candidate sets aren't worth the cross-encoder hop."""
    router = RerankRouter(rerank_threshold=10, exact_match_top_n=3)
    candidates = [_chunk("pkg.x.unrelated") for _ in range(5)]
    assert router.should_rerank("anything", candidates) is False


def test_skip_when_disabled():
    router = RerankRouter(rerank_threshold=10, exact_match_top_n=3, enabled=False)
    candidates = [_chunk("pkg.x.unrelated") for _ in range(50)]
    assert router.should_rerank("anything", candidates) is False


def test_exact_match_only_checked_within_top_n_window():
    """If exact match exists but is at rank 8 with window=3, do NOT skip."""
    router = RerankRouter(rerank_threshold=10, exact_match_top_n=3)
    candidates = (
        [_chunk("pkg.unrelated") for _ in range(7)]
        + [_chunk("pkg.parseHttpRequest")]
        + [_chunk("pkg.unrelated") for _ in range(20)]
    )
    assert router.should_rerank("parseHttpRequest", candidates) is True


def test_exact_match_match_is_case_insensitive_on_last_segment():
    router = RerankRouter(rerank_threshold=10, exact_match_top_n=3)
    candidates = [_chunk("pkg.module.HTTPCLIENT")] + [_chunk("pkg.unrelated") for _ in range(20)]
    assert router.should_rerank("httpclient", candidates) is False


@pytest.mark.parametrize("query", ["", "   ", "\n"])
def test_empty_query_skips_rerank(query: str):
    router = RerankRouter(rerank_threshold=10, exact_match_top_n=3)
    candidates = [_chunk("pkg.x.unrelated") for _ in range(50)]
    assert router.should_rerank(query, candidates) is False
