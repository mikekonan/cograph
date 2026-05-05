"""Lexical retrieval components for Phase 7d.

Two classes:

* ``LexicalRetriever`` — BM25-like ``ts_rank_cd`` queries over the tsvector
  columns produced by migrations 0015 (``content_tsv``, english config) and
  0018 (``content_tsv_simple``, simple config).  The code store uses the
  simple config because English stemming hurts code identifiers.

* ``SymbolLookup`` — pg_trgm similarity on ``code_nodes.qualified_name`` to
  find fuzzy matches BM25 misses (``foo_bar_baz`` ↔ ``foobarbaz``).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from backend.app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

Store = Literal["code", "repo_docs", "banks", "bank_facts", "md_collections"]


def _bind_temporal(stmt: TextClause) -> TextClause:
    """Pin OIDs on the `as_of` / `since` / `until` parameters.

    asyncpg raises `AmbiguousParameterError` when a None-valued parameter
    appears only in `:foo IS NULL OR col <op> :foo` because Postgres can't
    infer a type from `IS NULL` alone. Binding `TIMESTAMPTZ` explicitly
    sets the OID at protocol level so the prepared-statement plan stays
    valid even when the value is None.
    """
    return stmt.bindparams(
        bindparam("as_of", type_=DateTime(timezone=True)),
        bindparam("since", type_=DateTime(timezone=True)),
        bindparam("until", type_=DateTime(timezone=True)),
    )


class LexicalRetriever:
    """BM25 / ts_rank_cd retrieval over the per-store tsvector columns.

    The query text is always passed as a bound parameter — never interpolated —
    so adversarial tsquery operators (``&|!():*``) can't break out of
    ``plainto_tsquery``'s sanitisation.
    """

    async def search(
        self,
        session: AsyncSession,
        *,
        store: Store,
        query_text: str,
        repository_id: UUID | None = None,
        bank_ids: list[UUID] | None = None,
        collection_id: UUID | None = None,
        top_k: int = 10,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RetrievedChunk]:
        if not query_text or not query_text.strip():
            return []

        if store == "code":
            if repository_id is None:
                return []
            return await self._search_code(
                session,
                query_text,
                repository_id,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        if store == "repo_docs":
            if repository_id is None:
                return []
            return await self._search_repo_docs(
                session,
                query_text,
                repository_id,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        if store == "banks":
            if not bank_ids:
                return []
            return await self._search_banks(
                session,
                query_text,
                bank_ids,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        if store == "bank_facts":
            if not bank_ids:
                return []
            return await self._search_bank_facts(
                session,
                query_text,
                bank_ids,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        if store == "md_collections":
            if collection_id is None:
                return []
            return await self._search_md_collections(
                session,
                query_text,
                collection_id,
                top_k,
                as_of=as_of,
                since=since,
                until=until,
            )
        raise ValueError(f"unknown store: {store!r}")

    async def _search_code(
        self,
        session: AsyncSession,
        query_text: str,
        repository_id: UUID,
        top_k: int,
        *,
        as_of: datetime | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[RetrievedChunk]:
        # 'simple' regconfig + content_tsv_simple keeps code identifiers intact
        # (no English stemming, no stopword stripping). See migration 0018.
        stmt = text(
            """
            SELECT
                cn.id AS chunk_id,
                cn.content AS content,
                cn.qualified_name AS qualified_name,
                cn.file_path AS file_path,
                cn.language AS language,
                cn.start_line AS start_line,
                cn.end_line AS end_line,
                ts_rank_cd(cn.content_tsv_simple, plainto_tsquery('simple', :query_text)) AS score
            FROM code_nodes cn
            WHERE cn.repository_id = :repo_id
              AND cn.content_tsv_simple @@ plainto_tsquery('simple', :query_text)
              AND (CAST(:as_of AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:until AS timestamp))
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "query_text": query_text,
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

    async def _search_repo_docs(
        self,
        session: AsyncSession,
        query_text: str,
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
                ts_rank_cd(rdc.content_tsv, plainto_tsquery('english', :query_text)) AS score
            FROM repo_document_chunks rdc
            JOIN repo_documents rd ON rd.id = rdc.document_id
            WHERE rd.repository_id = :repo_id
              AND rdc.content_tsv @@ plainto_tsquery('english', :query_text)
              AND (CAST(:as_of AS timestamp) IS NULL OR rd.updated_at <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR rd.updated_at >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR rd.updated_at <= CAST(:until AS timestamp))
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "query_text": query_text,
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

    async def _search_banks(
        self,
        session: AsyncSession,
        query_text: str,
        bank_ids: list[UUID],
        top_k: int,
        *,
        as_of: datetime | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[RetrievedChunk]:
        stmt = text(
            """
            SELECT
                bdc.id AS chunk_id,
                bdc.content AS content,
                bdc.chunk_index AS chunk_index,
                bdc.heading_path AS heading_path,
                bd.title AS title,
                bd.bank_id AS bank_id,
                b.name AS bank_name,
                ts_rank_cd(bdc.content_tsv, plainto_tsquery('english', :query_text)) AS score
            FROM bank_document_chunks bdc
            JOIN bank_documents bd ON bd.id = bdc.document_id
            JOIN banks b ON b.id = bd.bank_id
            WHERE bd.bank_id = ANY(:bank_ids)
              AND bdc.content_tsv @@ plainto_tsquery('english', :query_text)
              AND (CAST(:as_of AS timestamp) IS NULL OR bd.updated_at <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR bd.updated_at >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR bd.updated_at <= CAST(:until AS timestamp))
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "query_text": query_text,
                "bank_ids": list(bank_ids),
                "top_k": top_k,
                "as_of": as_of,
                "since": since,
                "until": until,
            },
        )
        return [
            RetrievedChunk(
                store="banks",
                chunk_id=row["chunk_id"],
                content=row["content"],
                score=float(row["score"]),
                metadata={
                    "bank_id": row["bank_id"],
                    "bank_name": row["bank_name"],
                    "title": row["title"],
                    "chunk_index": row["chunk_index"],
                    "heading_path": row["heading_path"],
                },
            )
            for row in result.mappings().all()
        ]

    async def _search_md_collections(
        self,
        session: AsyncSession,
        query_text: str,
        collection_id: UUID,
        top_k: int,
        *,
        as_of: datetime | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[RetrievedChunk]:
        stmt = text(
            """
            SELECT * FROM (
                -- BM25 primary path
                SELECT
                    mc.id AS chunk_id,
                    mc.content AS content,
                    mc.chunk_index AS chunk_index,
                    mc.heading_path AS heading_path,
                    md.id AS document_id,
                    md.source_key AS source_key,
                    md.title AS title,
                    ts_rank_cd(mc.content_tsv, plainto_tsquery('english', :query_text)) AS score
                FROM md_chunks mc
                JOIN md_documents md ON md.id = mc.document_id
                WHERE md.collection_id = :collection_id
                  AND mc.content_tsv @@ plainto_tsquery('english', :query_text)
                  AND (CAST(:as_of AS timestamp) IS NULL OR md.updated_at <= CAST(:as_of AS timestamp))
                  AND (CAST(:since AS timestamp) IS NULL OR md.updated_at >= CAST(:since AS timestamp))
                  AND (CAST(:until AS timestamp) IS NULL OR md.updated_at <= CAST(:until AS timestamp))
                UNION ALL
                -- ILIKE fallback for partial literal matches BM25 may miss
                SELECT
                    mc.id AS chunk_id,
                    mc.content AS content,
                    mc.chunk_index AS chunk_index,
                    mc.heading_path AS heading_path,
                    md.id AS document_id,
                    md.source_key AS source_key,
                    md.title AS title,
                    0.01 AS score
                FROM md_chunks mc
                JOIN md_documents md ON md.id = mc.document_id
                WHERE md.collection_id = :collection_id
                  AND mc.content ILIKE :ilike_query
                  AND NOT (mc.content_tsv @@ plainto_tsquery('english', :query_text))
                  AND (CAST(:as_of AS timestamp) IS NULL OR md.updated_at <= CAST(:as_of AS timestamp))
                  AND (CAST(:since AS timestamp) IS NULL OR md.updated_at >= CAST(:since AS timestamp))
                  AND (CAST(:until AS timestamp) IS NULL OR md.updated_at <= CAST(:until AS timestamp))
            ) combined
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt,
            {
                "query_text": query_text,
                "ilike_query": f"%{query_text}%",
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

    async def _search_bank_facts(
        self,
        session: AsyncSession,
        query_text: str,
        bank_ids: list[UUID],
        top_k: int,
        *,
        as_of: datetime | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[RetrievedChunk]:
        stmt = text(
            """
            SELECT
                bf.id AS chunk_id,
                bf.statement AS content,
                bf.document_id AS document_id,
                bf.heading_path AS heading_path,
                bd.title AS title,
                bf.bank_id AS bank_id,
                b.name AS bank_name,
                ts_rank_cd(bf.content_tsv, plainto_tsquery('english', :query_text)) AS score
            FROM bank_facts bf
            JOIN bank_documents bd ON bd.id = bf.document_id
            JOIN banks b ON b.id = bf.bank_id
            WHERE bf.bank_id = ANY(:bank_ids)
              AND bf.content_tsv @@ plainto_tsquery('english', :query_text)
              AND (CAST(:as_of AS timestamp) IS NULL OR bd.updated_at <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR bd.updated_at >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR bd.updated_at <= CAST(:until AS timestamp))
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "query_text": query_text,
                "bank_ids": list(bank_ids),
                "top_k": top_k,
                "as_of": as_of,
                "since": since,
                "until": until,
            },
        )
        return [
            RetrievedChunk(
                store="bank_facts",
                chunk_id=row["chunk_id"],
                content=row["content"],
                score=float(row["score"]),
                metadata={
                    "bank_id": row["bank_id"],
                    "bank_name": row["bank_name"],
                    "document_id": row["document_id"],
                    "title": row["title"],
                    "heading_path": row["heading_path"],
                },
            )
            for row in result.mappings().all()
        ]


class SymbolLookup:
    """Fuzzy symbol-name lookup via pg_trgm similarity on ``qualified_name``.

    Catches the case where the user types ``foobarbaz`` but the symbol is
    ``foo_bar_baz`` — BM25 won't connect them because token boundaries don't
    align.  The trigram index from migration 0018 makes this cheap.
    """

    def __init__(self, similarity_threshold: float = 0.3) -> None:
        self.similarity_threshold = float(similarity_threshold)

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
    ) -> list[RetrievedChunk]:
        if not query_text or not query_text.strip():
            return []

        stmt = text(
            """
            SELECT
                cn.id AS chunk_id,
                cn.content AS content,
                cn.qualified_name AS qualified_name,
                cn.file_path AS file_path,
                cn.language AS language,
                cn.start_line AS start_line,
                cn.end_line AS end_line,
                similarity(cn.qualified_name, :query_text) AS score
            FROM code_nodes cn
            WHERE cn.repository_id = :repo_id
              AND cn.qualified_name % :query_text
              AND similarity(cn.qualified_name, :query_text) >= :threshold
              AND (CAST(:as_of AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:until AS timestamp))
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "query_text": query_text,
                "repo_id": repository_id,
                "threshold": self.similarity_threshold,
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
