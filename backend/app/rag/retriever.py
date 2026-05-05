"""RAG retriever — hybrid kNN + BM25 via Reciprocal Rank Fusion across code / repo_docs / banks."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


Store = Literal["code", "repo_docs", "banks"]

_ALL_STORES: frozenset[Store] = frozenset({"code", "repo_docs", "banks"})
EMBEDDING_DIM = 1536

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class RetrievedChunk:
    store: Store
    chunk_id: UUID
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


def _format_vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(v) for v in embedding) + "]"


def _rrf_merge(
    vector_hits: list[RetrievedChunk],
    bm25_hits: list[RetrievedChunk],
    k: int = 60,
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion: score = sum(1 / (k + rank)) across vector and BM25 lists."""
    scores: dict[UUID, float] = {}
    chunks_by_id: dict[UUID, RetrievedChunk] = {}
    vector_ranks: dict[UUID, int] = {}
    bm25_ranks: dict[UUID, int] = {}

    for rank, hit in enumerate(vector_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        chunks_by_id[hit.chunk_id] = hit
        vector_ranks[hit.chunk_id] = rank

    for rank, hit in enumerate(bm25_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
        chunks_by_id.setdefault(hit.chunk_id, hit)
        bm25_ranks[hit.chunk_id] = rank

    merged = []
    for cid, score in sorted(scores.items(), key=lambda x: (-x[1], str(x[0]))):
        chunk = chunks_by_id[cid]
        meta = dict(chunk.metadata)
        meta["vector_rank"] = vector_ranks.get(cid)
        meta["bm25_rank"] = bm25_ranks.get(cid)
        merged.append(replace(chunk, score=score, metadata=meta))
    return merged


class RagRetriever:
    """Hybrid kNN + BM25 retriever via RRF across code_embeddings, repo_document_chunks, and bank_document_chunks."""

    async def retrieve(
        self,
        session: AsyncSession,
        query_text: str,
        query_embedding: list[float],
        *,
        repository_id: UUID | None = None,
        bank_ids: list[UUID] | None = None,
        top_k: int = 10,
        stores: set[Store] | None = None,
        rrf_k: int = 60,
    ) -> list[RetrievedChunk]:
        active = set(stores) if stores is not None else set(_ALL_STORES)
        if len(query_embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"query_embedding must have {EMBEDDING_DIM} dimensions, got {len(query_embedding)}"
            )
        qvec = _format_vector_literal(query_embedding)
        use_bm25 = bool(query_text and query_text.strip())

        store_tasks: list[tuple[Store, UUID | None, list[UUID] | None]] = []
        if "code" in active and repository_id is not None:
            store_tasks.append(("code", repository_id, None))
        if "repo_docs" in active and repository_id is not None:
            store_tasks.append(("repo_docs", repository_id, None))
        if "banks" in active and bank_ids:
            store_tasks.append(("banks", None, list(bank_ids)))

        if not store_tasks:
            return []

        all_merged: list[RetrievedChunk] = []
        for store_name, repo_id_arg, bank_ids_arg in store_tasks:
            vector_hits: list[RetrievedChunk] = []
            bm25_hits: list[RetrievedChunk] = []

            try:
                if store_name == "code":
                    vector_hits = await self._vector_code(session, qvec, repo_id_arg, top_k)
                elif store_name == "repo_docs":
                    vector_hits = await self._vector_repo_docs(session, qvec, repo_id_arg, top_k)
                else:
                    vector_hits = await self._vector_banks(session, qvec, bank_ids_arg, top_k)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("rag store %r vector query failed", store_name, exc_info=exc)

            if use_bm25:
                try:
                    if store_name == "code":
                        bm25_hits = await self._bm25_code(session, query_text, repo_id_arg, top_k)
                    elif store_name == "repo_docs":
                        bm25_hits = await self._bm25_repo_docs(session, query_text, repo_id_arg, top_k)
                    else:
                        bm25_hits = await self._bm25_banks(session, query_text, bank_ids_arg, top_k)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("rag store %r bm25 query failed", store_name, exc_info=exc)
                all_merged.extend(_rrf_merge(vector_hits, bm25_hits, k=rrf_k))
            else:
                # Vector-only mode: preserve cosine similarity ordering, don't flatten scores via RRF
                all_merged.extend(sorted(vector_hits, key=lambda c: c.score, reverse=True)[:top_k])

        all_merged.sort(key=lambda c: c.score, reverse=True)
        return all_merged[:top_k]

    # --- vector queries ---

    async def _vector_code(
        self,
        session: AsyncSession,
        qvec: str,
        repository_id: UUID,
        top_k: int,
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
            ORDER BY ce.embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt, {"qvec": qvec, "repo_id": repository_id, "top_k": top_k}
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

    async def _bm25_code(
        self,
        session: AsyncSession,
        query_text: str,
        repository_id: UUID,
        top_k: int,
    ) -> list[RetrievedChunk]:
        # NOTE: 'english' tokenizer poorly fits code identifiers (snake_case/camelCase
        # split, programming reserved words stem incorrectly). Evaluate `simple` secondary
        # tsvector or per-store dictionary config.
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
                ts_rank(cn.content_tsv, plainto_tsquery('english', :query_text)) AS score
            FROM code_nodes cn
            WHERE cn.repository_id = :repo_id
              AND cn.content_tsv @@ plainto_tsquery('english', :query_text)
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt, {"query_text": query_text, "repo_id": repository_id, "top_k": top_k}
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

    async def _vector_repo_docs(
        self,
        session: AsyncSession,
        qvec: str,
        repository_id: UUID,
        top_k: int,
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
            ORDER BY rdc.embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt, {"qvec": qvec, "repo_id": repository_id, "top_k": top_k}
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

    async def _bm25_repo_docs(
        self,
        session: AsyncSession,
        query_text: str,
        repository_id: UUID,
        top_k: int,
    ) -> list[RetrievedChunk]:
        # NOTE: 'english' tokenizer poorly fits code identifiers (snake_case/camelCase
        # split, programming reserved words stem incorrectly). Evaluate `simple` secondary
        # tsvector or per-store dictionary config.
        stmt = text(
            """
            SELECT
                rdc.id AS chunk_id,
                rdc.content AS content,
                rdc.chunk_index AS chunk_index,
                rdc.heading_path AS heading_path,
                rd.file_path AS file_path,
                rd.title AS title,
                ts_rank(rdc.content_tsv, plainto_tsquery('english', :query_text)) AS score
            FROM repo_document_chunks rdc
            JOIN repo_documents rd ON rd.id = rdc.document_id
            WHERE rd.repository_id = :repo_id
              AND rdc.content_tsv @@ plainto_tsquery('english', :query_text)
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt, {"query_text": query_text, "repo_id": repository_id, "top_k": top_k}
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

    async def _vector_banks(
        self,
        session: AsyncSession,
        qvec: str,
        bank_ids: list[UUID],
        top_k: int,
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
                1 - (bdc.embedding <=> CAST(:qvec AS vector)) AS score
            FROM bank_document_chunks bdc
            JOIN bank_documents bd ON bd.id = bdc.document_id
            JOIN banks b ON b.id = bd.bank_id
            WHERE bd.bank_id = ANY(:bank_ids)
              AND bdc.embedding IS NOT NULL
            ORDER BY bdc.embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt,
            {"qvec": qvec, "bank_ids": list(bank_ids), "top_k": top_k},
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

    async def _bm25_banks(
        self,
        session: AsyncSession,
        query_text: str,
        bank_ids: list[UUID],
        top_k: int,
    ) -> list[RetrievedChunk]:
        # NOTE: 'english' tokenizer poorly fits code identifiers (snake_case/camelCase
        # split, programming reserved words stem incorrectly). Evaluate `simple` secondary
        # tsvector or per-store dictionary config.
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
                ts_rank(bdc.content_tsv, plainto_tsquery('english', :query_text)) AS score
            FROM bank_document_chunks bdc
            JOIN bank_documents bd ON bd.id = bdc.document_id
            JOIN banks b ON b.id = bd.bank_id
            WHERE bd.bank_id = ANY(:bank_ids)
              AND bdc.content_tsv @@ plainto_tsquery('english', :query_text)
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        result = await session.execute(
            stmt,
            {"query_text": query_text, "bank_ids": list(bank_ids), "top_k": top_k},
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
