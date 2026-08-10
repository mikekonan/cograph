"""N-way Reciprocal Rank Fusion for hybrid retrieval (Phase 7d).

The 2-way ``_rrf_merge`` in retriever.py only fuses {vector, BM25}.  HybridRetriever
needs to fuse {vector, lexical, symbol} — and the door must stay open for more
streams later (e.g., ``ast_summary`` in Phase 7e).  ``rrf_merge_streams`` is a
generic, deterministic, capped N-way RRF that supersedes the 2-way helper.

Score formula (standard RRF, Cormack et al. 2009):

    score(c) = sum over streams s in which c appears of  1 / (k + rank_s(c))

Tie-break by ``str(chunk_id)`` ascending so the order is reproducible across
runs (Python set/dict iteration order isn't seeded but ``sorted`` on a stable
key is).
"""
from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from backend.app.rag.retriever import RetrievedChunk


def rrf_merge_streams(
    streams: list[list[RetrievedChunk]],
    *,
    k: int = 60,
    candidate_cap: int | None = None,
    stream_names: list[str] | None = None,
) -> list[RetrievedChunk]:
    """Fuse ``len(streams)`` ranked lists into a single ranked list via RRF.

    Args:
        streams: per-source ranked candidate lists.  Each list is treated as
            its own ranking starting from rank 1.  Empty inner lists are fine.
        k: RRF constant.  Higher k flattens contribution of top ranks; 60 is
            the canonical default from the paper and matches the existing
            ``_rrf_merge``.  Must be > 0.
        candidate_cap: per-stream truncation applied BEFORE fusion.  ``None``
            disables capping.  Useful to bound the cost of merging long tails.
        stream_names: optional labels recorded in each merged chunk's metadata
            as ``f"{name}_rank"``.  Length must match ``len(streams)`` if
            given.  Defaults to no per-stream rank tags.

    Returns:
        Chunks ordered by descending fused score, deterministic on ties.
    """
    if k <= 0:
        raise ValueError(f"RRF k must be > 0, got {k}")
    if not streams:
        return []
    if stream_names is not None and len(stream_names) != len(streams):
        raise ValueError(
            f"stream_names length {len(stream_names)} must match streams {len(streams)}"
        )

    scores: dict[UUID, float] = {}
    chunks_by_id: dict[UUID, RetrievedChunk] = {}
    per_stream_ranks: dict[UUID, dict[str, int]] = {}

    for stream_idx, stream in enumerate(streams):
        capped = stream if candidate_cap is None else stream[:candidate_cap]
        label = stream_names[stream_idx] if stream_names else None
        for rank, hit in enumerate(capped, start=1):
            cid = hit.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            chunks_by_id.setdefault(cid, hit)
            if label is not None:
                per_stream_ranks.setdefault(cid, {})[f"{label}_rank"] = rank

    merged: list[RetrievedChunk] = []
    for cid, score in sorted(scores.items(), key=lambda x: (-x[1], str(x[0]))):
        chunk = chunks_by_id[cid]
        meta = dict(chunk.metadata)
        if cid in per_stream_ranks:
            meta.update(per_stream_ranks[cid])
        merged.append(replace(chunk, score=score, metadata=meta))
    return merged
