"""Rerank routing policy (Phase 7d).

The cross-encoder is the most expensive step in the retrieval pipeline.  We
skip it when it can't materially help:

* The candidate set is too small for the model's pairwise comparisons to
  matter (a tiny set is already well-ordered by RRF alone).
* A top-N candidate is an exact match for the query symbol — there's nothing
  for the reranker to discover that BM25 + symbol lookup didn't already.
* The reranker itself is disabled in config.
"""
from __future__ import annotations

from backend.app.rag.retriever import RetrievedChunk


class RerankRouter:
    """Decides whether a candidate set warrants the cross-encoder pass.

    Args:
        rerank_threshold: minimum candidate count to bother reranking.
            Candidate sets smaller than this skip the reranker outright.
        exact_match_top_n: how many top-ranked candidates to scan for an
            exact symbol match.  If a match is found within this window the
            reranker is skipped — the symbol lookup already nailed it.
        enabled: master kill-switch, mirrors ``RetrievalSettings.rerank.enabled``.
    """

    def __init__(
        self,
        *,
        rerank_threshold: int = 50,
        exact_match_top_n: int = 3,
        enabled: bool = True,
    ) -> None:
        self.rerank_threshold = int(rerank_threshold)
        self.exact_match_top_n = int(exact_match_top_n)
        self.enabled = bool(enabled)

    def should_rerank(self, query: str, candidates: list[RetrievedChunk]) -> bool:
        if not self.enabled:
            return False
        if not query or not query.strip():
            return False
        if len(candidates) < self.rerank_threshold:
            return False
        if self._has_exact_symbol_match(query, candidates[: self.exact_match_top_n]):
            return False
        return True

    def _has_exact_symbol_match(
        self, query: str, top_candidates: list[RetrievedChunk]
    ) -> bool:
        needle = query.strip().lower()
        for c in top_candidates:
            qname = c.metadata.get("qualified_name")
            if not qname:
                continue
            # Compare against the last segment of the qualified name (the
            # symbol's local identifier), case-insensitively.  ``pkg.module.Foo``
            # → ``Foo``.
            last_segment = str(qname).rsplit(".", 1)[-1]
            if last_segment.lower() == needle:
                return True
        return False
