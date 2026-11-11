"""Lexical retrieval components for Phase 7d.

Two classes:

* ``LexicalRetriever`` — BM25-like ``ts_rank_cd`` queries over the tsvector
  columns produced by migrations 0015 (``content_tsv``, english config) and
  0018 (``content_tsv_simple``, simple config).  The code store uses the
  simple config because English stemming hurts code identifiers.  It matches
  any query word, rows with every word first.

* ``SymbolLookup`` — pg_trgm similarity on ``code_nodes.qualified_name`` to
  find fuzzy matches BM25 misses (``foo_bar_baz`` ↔ ``foobarbaz``).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import DateTime, bindparam, case, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from backend.app.models.code_node import NODE_KIND_ORDER, CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.rag.retriever import RetrievedChunk
from backend.app.rag.snippet import extract_query_terms

logger = logging.getLogger(__name__)

Store = Literal["code", "repo_docs", "md_collections"]

# A query matches on any one word, so it can match much of a repository.
# Past this many, lexical hits only push vector hits out of the fused list.
_CODE_CANDIDATE_CAP = 50


def _tsquery_operand(term: str) -> str:
    # Quoted, so `to_tsquery` hands the term to the parser that built the
    # index: `a.b`, `a-b`, `a_b` split the way the code did, and `&|!():*`
    # stay text.
    quoted = "'" + term.replace("\\", "\\\\").replace("'", "''") + "'"
    # Prefix match from 5 characters: `label` finds `labels`. A shorter
    # prefix matches too many words.
    return quoted + ":*" if len(term) >= 5 else quoted


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

    Query text is always a bound parameter, never interpolated. The doc stores
    pass it to ``plainto_tsquery``; the code store passes quoted terms from
    ``extract_query_terms`` to ``to_tsquery``. Neither lets ``&|!():*`` in the
    query act as an operator.
    """

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
        path_prefix: str = "",
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
                path_prefix=path_prefix,
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
        path_prefix: str,
    ) -> list[RetrievedChunk]:
        terms = extract_query_terms(query_text)
        if not terms:
            return []
        operands = [_tsquery_operand(term) for term in terms]
        # 'simple' regconfig + content_tsv_simple keeps code identifiers intact
        # (no English stemming, no stopword stripping). See migration 0018.
        # Any word matches; rows with every word sort first, then by rank.
        # ts_rank_cd without a normalisation flag: a long body is not
        # penalised for its length.
        # Module rows hold a whole file, so they contain most words and would
        # top every query; their functions and types are rows of their own.
        # starts_with, not LIKE: `_` and `%` in a path stay literal. An empty
        # prefix matches every path.
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
                cn.content_tsv_simple @@ to_tsquery('simple', :all_terms) AS has_all_terms,
                ts_rank_cd(cn.content_tsv_simple, to_tsquery('simple', :any_term)) AS score
            FROM code_nodes cn
            WHERE cn.repository_id = :repo_id
              AND cn.content_tsv_simple @@ to_tsquery('simple', :any_term)
              AND cn.node_type <> 'module'
              AND starts_with(cn.file_path, :path_prefix)
              AND (CAST(:as_of AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:as_of AS timestamp))
              AND (CAST(:since AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) >= CAST(:since AS timestamp))
              AND (CAST(:until AS timestamp) IS NULL OR COALESCE(cn.last_changed_at, cn.created_at) <= CAST(:until AS timestamp))
            ORDER BY has_all_terms DESC, score DESC
            LIMIT :top_k
            """
        )
        stmt = _bind_temporal(stmt)
        result = await session.execute(
            stmt,
            {
                "any_term": " | ".join(operands),
                "all_terms": " & ".join(operands),
                "repo_id": repository_id,
                "top_k": min(top_k, _CODE_CANDIDATE_CAP),
                "path_prefix": path_prefix,
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
        path_prefix: str = "",
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
              AND starts_with(cn.file_path, :path_prefix)
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
                "path_prefix": path_prefix,
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

    async def exact(
        self,
        session: AsyncSession,
        *,
        name: str,
        repository_id: UUID,
        top_k: int = 10,
        path_prefix: str = "",
        suffix: str = "",
    ) -> list[RetrievedChunk]:
        """Nodes named exactly `name`; rides `idx_code_nodes_repo_name`.

        Similarity on the qualified name prefers a short path: a mock in
        `mocks/` beat the implementation deep in `internal/`. The plain name
        has no path in it.

        A common name (`Handle`, `New`) has hundreds of nodes, so the order
        picks the `top_k`: the qualified name ending in `suffix` (the query
        `Type.Method`), then product code before tests and generated code,
        then by path.
        """
        rows = await session.execute(
            select(
                CodeNode.id,
                CodeNode.content,
                CodeNode.qualified_name,
                CodeNode.file_path,
                CodeNode.language,
                CodeNode.start_line,
                CodeNode.end_line,
            )
            .where(
                CodeNode.repository_id == repository_id,
                CodeNode.name == name,
                CodeNode.node_type != CodeNodeType.MODULE,
                CodeNode.file_path.startswith(path_prefix, autoescape=True),
            )
            .order_by(
                case(
                    (CodeNode.qualified_name.endswith(suffix, autoescape=True), 0),
                    else_=1,
                ),
                NODE_KIND_ORDER,
                CodeNode.file_path,
                CodeNode.start_line,
            )
            .limit(top_k)
        )
        return [
            RetrievedChunk(
                store="code",
                chunk_id=row.id,
                content=row.content,
                score=1.0,
                metadata={
                    "qualified_name": row.qualified_name,
                    "file_path": row.file_path,
                    "language": row.language,
                    "start_line": row.start_line,
                    "end_line": row.end_line,
                },
            )
            for row in rows
        ]
