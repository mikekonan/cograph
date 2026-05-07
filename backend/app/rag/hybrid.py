"""HybridRetriever — fan-out vector + lexical + symbol → RRF → optional rerank.

The Phase 7c retriever (``RagRetriever``) fuses 2 streams (vector + BM25) per
store.  Phase 7d generalises to 3 streams (adds pg_trgm symbol lookup) and
adds an optional cross-encoder rerank gated by ``RerankRouter``.

This module is the orchestration layer; the heavy lifting lives in:
  * ``vector`` — :class:`VectorRetriever` (kNN over pgvector)
  * ``lexical`` — :class:`backend.app.rag.lexical.LexicalRetriever`
  * ``symbol`` — :class:`backend.app.rag.lexical.SymbolLookup`
  * ``reranker`` — :class:`backend.app.rag.rerank.Reranker`
  * ``router`` — :class:`backend.app.rag.router.RerankRouter`
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from backend.app.rag.fusion import rrf_merge_streams
from backend.app.rag.retriever import EMBEDDING_DIM, RetrievedChunk, _format_vector_literal

logger = logging.getLogger(__name__)


def _bind_temporal(stmt: TextClause) -> TextClause:
    """Pin OIDs on the `as_of` / `since` / `until` parameters.

    asyncpg fails with `AmbiguousParameterError` when a parameter is referenced
    only in `:foo IS NULL OR col <op> :foo` and the value is None — Postgres
    can't infer a type from `IS NULL` alone. Binding with an explicit
    `TIMESTAMPTZ` tells asyncpg the OID up front.
    """
    return stmt.bindparams(
        bindparam("as_of", type_=DateTime(timezone=True)),
        bindparam("since", type_=DateTime(timezone=True)),
        bindparam("until", type_=DateTime(timezone=True)),
    )

Store = Literal["code", "repo_docs", "md_collections"]
_ALL_STORES: frozenset[Store] = frozenset({"code", "repo_docs", "md_collections"})


# ---------------------------------------------------------------------------
# Protocols — what HybridRetriever expects from each collaborator
# ---------------------------------------------------------------------------


class _VectorSearcher(Protocol):
    async def search(
        self,
        session: AsyncSession,
        *,
        store: Store,
        query_embedding: list[float],
        repository_id: UUID | None = None,
        collection_id: UUID | None = None,
        top_k: int = 10,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievedChunk]: ...


class _LexicalSearcher(Protocol):
    async def search(
        self,
        session: AsyncSession,
        *,
        store: Store,
        query_text: str,
        repository_id: UUID | None = None,
        collection_id: UUID | None = None,
        top_k: int = 10,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievedChunk]: ...


class _SymbolSearcher(Protocol):
    async def search(
        self,
        session: AsyncSession,
        *,
        query_text: str,
        repository_id: UUID,
        top_k: int = 10,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievedChunk]: ...


class _Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]: ...


class _Router(Protocol):
    def should_rerank(self, query: str, candidates: list[RetrievedChunk]) -> bool: ...


# ---------------------------------------------------------------------------
# VectorRetriever — adapter around the SQL queries in retriever.py
# ---------------------------------------------------------------------------


class VectorRetriever:
    """Vector kNN search per store, sharing the SQL with ``RagRetriever``.

    Kept as a separate class so HybridRetriever can swap it for stubs in tests
    and so the search dispatch reads naturally (vector.search(store=...)).
    """

    async def search(
        self,
        session: AsyncSession,
        *,
        store: Store,
        query_embedding: list[float],
        repository_id: UUID | None = None,
        collection_id: UUID | None = None,
        top_k: int = 10,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievedChunk]:
        if len(query_embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"query_embedding must have {EMBEDDING_DIM} dimensions, got {len(query_embedding)}"
            )
        qvec = _format_vector_literal(query_embedding)
        if store == "code":
            if repository_id is None:
                return []
            return await self._code(
                session,
                qvec,
                repository_id,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        if store == "repo_docs":
            if repository_id is None:
                return []
            return await self._repo_docs(
                session,
                qvec,
                repository_id,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        if store == "md_collections":
            if collection_id is None:
                return []
            return await self._md_collections(
                session,
                qvec,
                collection_id,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        raise ValueError(f"unknown store: {store!r}")

    async def _code(
        self,
        session: AsyncSession,
        qvec: str,
        repository_id: UUID,
        top_k: int,
        *,
        as_of: datetime | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[RetrievedChunk]:
        stmt = text(
            """
            SELECT
                ce.code_node_id AS chunk_id,
                cn.content AS content,
                cn.qualified_name AS qualified_name,
                cn.file_path AS file_path,
                cn.language AS language,
                cn.start_line AS start_line,
                cn.end_line AS end_line,
                1 - (ce.embedding <=> CAST(:qvec AS vector)) AS score
            FROM code_embeddings ce
            JOIN code_nodes cn ON cn.id = ce.code_node_id
            WHERE cn.repository_id = :repo_id
              AND ce.embedding IS NOT NULL
              AND (CAST(:as_of AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:until AS timestamp))
            ORDER BY ce.embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "qvec": qvec,
                "repo_id": repository_id,
                "top_k": top_k,
                "as_of": as_of,
                "since": since,
                "until": until,
            },
        )
        return [
            RetrievedChunk(
                store="code",
                chunk_id=row["chunk_id"],
                content=row["content"],
                score=float(row["score"]),
                metadata={
                    "qualified_name": row["qualified_name"],
                    "file_path": row["file_path"],
                    "language": row["language"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                },
            )
            for row in result.mappings().all()
        ]

    async def _repo_docs(
        self,
        session: AsyncSession,
        qvec: str,
        repository_id: UUID,
        top_k: int,
        *,
        as_of: datetime | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[RetrievedChunk]:
        stmt = text(
            """
            SELECT
                rdc.id AS chunk_id,
                rdc.content AS content,
                rdc.chunk_index AS chunk_index,
                rdc.heading_path AS heading_path,
                rd.file_path AS file_path,
                rd.title AS title,
                1 - (rdc.embedding <=> CAST(:qvec AS vector)) AS score
            FROM repo_document_chunks rdc
            JOIN repo_documents rd ON rd.id = rdc.document_id
            WHERE rd.repository_id = :repo_id
              AND rdc.embedding IS NOT NULL
              AND (CAST(:as_of AS timestamp) IS NULL OR rd.updated_at <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR rd.updated_at >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR rd.updated_at <= CAST(:until AS timestamp))
            ORDER BY rdc.embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "qvec": qvec,
                "repo_id": repository_id,
                "top_k": top_k,
                "as_of": as_of,
                "since": since,
                "until": until,
            },
        )
        return [
            RetrievedChunk(
                store="repo_docs",
                chunk_id=row["chunk_id"],
                content=row["content"],
                score=float(row["score"]),
                metadata={
                    "file_path": row["file_path"],
                    "title": row["title"],
                    "chunk_index": row["chunk_index"],
                    "heading_path": row["heading_path"],
                },
            )
            for row in result.mappings().all()
        ]

    async def _md_collections(
        self,
        session: AsyncSession,
        qvec: str,
        collection_id: UUID,
        top_k: int,
        *,
        as_of: datetime | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[RetrievedChunk]:
        stmt = text(
            """
            SELECT
                mc.id AS chunk_id,
                mc.content AS content,
                mc.chunk_index AS chunk_index,
                mc.heading_path AS heading_path,
                md.id AS document_id,
                md.source_key AS source_key,
                md.title AS title,
                1 - (mc.embedding <=> CAST(:qvec AS vector)) AS score
            FROM md_chunks mc
            JOIN md_documents md ON md.id = mc.document_id
            WHERE md.collection_id = :collection_id
              AND mc.embedding IS NOT NULL
              AND (CAST(:as_of AS timestamp) IS NULL OR md.updated_at <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR md.updated_at >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR md.updated_at <= CAST(:until AS timestamp))
            ORDER BY mc.embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt,
            {
                "qvec": qvec,
                "collection_id": collection_id,
                "top_k": top_k,
                "as_of": as_of,
                "since": since,
                "until": until,
            },
        )
        return [
            RetrievedChunk(
                store="md_collections",
                chunk_id=row["chunk_id"],
                content=row["content"],
                score=float(row["score"]),
                metadata={
                    "document_id": row["document_id"],
                    "source_key": row["source_key"],
                    "title": row["title"],
                    "chunk_index": row["chunk_index"],
                    "heading_path": row["heading_path"],
                },
            )
            for row in result.mappings().all()
        ]


# ---------------------------------------------------------------------------
# HybridRetriever — orchestrator
# ---------------------------------------------------------------------------


class HybridRetriever:
    """Per-store fan-out → 3-way RRF fusion → optional cross-encoder rerank.

    Per active store, runs in parallel:
      * vector kNN (always)
      * lexical BM25 (only if query_text is non-empty)
      * symbol fuzzy lookup (code store only, only if query_text is non-empty)

    A failed stream logs a warning and is skipped — partial results are better
    than none.  Each stream is truncated to ``candidate_cap`` before fusion to
    bound RRF cost on long tails.
    """

    def __init__(
        self,
        *,
        vector: _VectorSearcher,
        lexical: _LexicalSearcher,
        symbol: _SymbolSearcher,
        reranker: _Reranker,
        router: _Router,
        rrf_k: int = 60,
        candidate_cap: int = 300,
    ) -> None:
        self.vector = vector
        self.lexical = lexical
        self.symbol = symbol
        self.reranker = reranker
        self.router = router
        self.rrf_k = int(rrf_k)
        self.candidate_cap = int(candidate_cap)

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        query_text: str,
        query_embedding: list[float],
        repository_id: UUID | None = None,
        collection_id: UUID | None = None,
        top_k: int = 10,
        stores: set[Store] | None = None,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievedChunk]:
        active = set(stores) if stores is not None else set(_ALL_STORES)
        use_text = bool(query_text and query_text.strip())

        # Determine which (store, kwargs) tuples to run.
        store_specs: list[tuple[Store, dict]] = []
        if "code" in active and repository_id is not None:
            store_specs.append(("code", {"repository_id": repository_id}))
        if "repo_docs" in active and repository_id is not None:
            store_specs.append(("repo_docs", {"repository_id": repository_id}))
        if "md_collections" in active and collection_id is not None:
            store_specs.append(("md_collections", {"collection_id": collection_id}))

        if not store_specs:
            return []

        all_merged: list[RetrievedChunk] = []
        for store_name, kwargs in store_specs:
            streams: list[list[RetrievedChunk]] = []
            stream_names: list[str] = []
            v_hits = await self._safe(
                self.vector.search(
                    session,
                    store=store_name,
                    query_embedding=query_embedding,
                    top_k=self.candidate_cap,
                    as_of=as_of,
                    since=since,
                    until=until,
                    **kwargs,
                ),
                stream="vector",
                store=store_name,
            )
            streams.append(v_hits)
            stream_names.append("vector")

            if use_text:
                l_hits = await self._safe(
                    self.lexical.search(
                        session,
                        store=store_name,
                        query_text=query_text,
                        top_k=self.candidate_cap,
                        as_of=as_of,
                        since=since,
                        until=until,
                        **kwargs,
                    ),
                    stream="lexical",
                    store=store_name,
                )
                streams.append(l_hits)
                stream_names.append("lexical")

                # Symbol lookup is code-only (qualified_name lives on code_nodes).
                if store_name == "code":
                    s_hits = await self._safe(
                        self.symbol.search(
                            session,
                            query_text=query_text,
                            repository_id=kwargs["repository_id"],
                            top_k=self.candidate_cap,
                            as_of=as_of,
                            since=since,
                            until=until,
                        ),
                        stream="symbol",
                        store=store_name,
                    )
                    streams.append(s_hits)
                    stream_names.append("symbol")

            merged = rrf_merge_streams(
                streams,
                k=self.rrf_k,
                candidate_cap=self.candidate_cap,
                stream_names=stream_names,
            )
            all_merged.extend(merged)

        # Cross-store sort by fused RRF score (each store's RRF scores are on
        # the same scale because they all use the same k).
        all_merged.sort(key=lambda c: c.score, reverse=True)

        # Optional rerank — gated by router policy.
        if self.router.should_rerank(query_text, all_merged):
            all_merged = await self.reranker.rerank(query_text, all_merged, top_k)
        else:
            all_merged = all_merged[:top_k]

        return all_merged

    @staticmethod
    async def _safe(coro, *, stream: str, store: str) -> list[RetrievedChunk]:
        """Await ``coro``; on failure log + return []. Cancellation propagates."""
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — graceful degradation
            logger.warning(
                "hybrid retrieval: store=%s stream=%s failed",
                store,
                stream,
                exc_info=exc,
            )
            return []
