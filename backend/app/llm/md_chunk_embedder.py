"""MdChunkEmbedderService — embeds md_chunks incrementally.

Skip predicate: existing chunk has same content_hash AND same model. A model
change forces re-embedding to prevent stale cross-model vectors.
"""

from __future__ import annotations

import hashlib
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.graph._chunking import chunked
from backend.app.llm.code_embedder import EmbedResult
from backend.app.llm.embedder import EmbedProvider
from backend.app.models.md_collection import MdChunk, MdDocument


class MdChunkEmbedderService:
    def __init__(self, provider: EmbedProvider, batch_size: int = 256) -> None:
        self._provider = provider
        self._batch_size = batch_size

    async def embed_collection(
        self,
        *,
        session: AsyncSession,
        collection_id: UUID,
        progress_callback: Callable[[int, int, str | None], Awaitable[object]]
        | None = None,
    ) -> EmbedResult:
        chunks = list(
            (
                await session.scalars(
                    select(MdChunk)
                    .join(MdDocument, MdChunk.document_id == MdDocument.id)
                    .where(MdDocument.collection_id == collection_id)
                )
            ).all()
        )

        if not chunks:
            return EmbedResult(
                embedded_nodes=0, skipped_nodes=0, model=self._provider.model
            )

        # Build chunk_id -> source_key mapping for progress reporting.
        # Chunked IN-lookup so collections with >32k documents don't blow
        # past asyncpg's placeholder cap.
        doc_ids = list({c.document_id for c in chunks})
        docs: list[MdDocument] = []
        for batch in chunked(doc_ids):
            docs.extend(
                (
                    await session.scalars(
                        select(MdDocument).where(MdDocument.id.in_(batch))
                    )
                ).all()
            )
        source_map = {d.id: d.source_key for d in docs}
        chunk_sources = {c.id: source_map.get(c.document_id) for c in chunks}

        return await self._embed_chunks(
            session=session,
            chunks=chunks,
            progress_callback=progress_callback,
            chunk_sources=chunk_sources,
        )

    async def embed_documents(
        self,
        *,
        session: AsyncSession,
        document_ids: list[UUID],
        progress_callback: Callable[[int, int, str | None], Awaitable[object]]
        | None = None,
    ) -> EmbedResult:
        if not document_ids:
            return EmbedResult(
                embedded_nodes=0, skipped_nodes=0, model=self._provider.model
            )

        # Chunked lookups: callers pass collection-scale document_ids
        # lists that can exceed asyncpg's 32767-placeholder cap.
        chunks: list[MdChunk] = []
        for batch in chunked(document_ids):
            chunks.extend(
                (
                    await session.scalars(
                        select(MdChunk).where(MdChunk.document_id.in_(batch))
                    )
                ).all()
            )

        if not chunks:
            return EmbedResult(
                embedded_nodes=0, skipped_nodes=0, model=self._provider.model
            )

        docs: list[MdDocument] = []
        for batch in chunked(document_ids):
            docs.extend(
                (
                    await session.scalars(
                        select(MdDocument).where(MdDocument.id.in_(batch))
                    )
                ).all()
            )
        source_map = {d.id: d.source_key for d in docs}
        chunk_sources = {c.id: source_map.get(c.document_id) for c in chunks}

        return await self._embed_chunks(
            session=session,
            chunks=chunks,
            progress_callback=progress_callback,
            chunk_sources=chunk_sources,
        )

    async def embed_all_orphaned(
        self,
        *,
        session: AsyncSession,
    ) -> EmbedResult:
        """Backfill any chunks with NULL embedding."""
        chunks = list(
            (
                await session.scalars(
                    select(MdChunk).where(MdChunk.embedding.is_(None))
                )
            ).all()
        )

        if not chunks:
            return EmbedResult(
                embedded_nodes=0, skipped_nodes=0, model=self._provider.model
            )

        return await self._embed_chunks(session=session, chunks=chunks)

    async def _embed_chunks(
        self,
        *,
        session: AsyncSession,
        chunks: list[MdChunk],
        progress_callback: Callable[[int, int, str | None], Awaitable[object]]
        | None = None,
        chunk_sources: dict[UUID, str | None] | None = None,
    ) -> EmbedResult:
        # Phase 1: determine what needs embedding and capture all required data
        # from ORM objects BEFORE releasing the DB connection.
        to_embed: list[tuple[UUID, str, str]] = []  # (chunk_id, text, computed_hash)
        skipped = 0
        for chunk in chunks:
            computed_hash = _content_hash(chunk.content)
            if (
                chunk.embedding is not None
                and chunk.model == self._provider.model
                and chunk.content_hash == computed_hash
            ):
                skipped += 1
                continue
            to_embed.append((chunk.id, _chunk_text(chunk), computed_hash))

        if not to_embed:
            return EmbedResult(
                embedded_nodes=0, skipped_nodes=skipped, model=self._provider.model
            )

        # Phase 2: release the DB connection before network round-trips.
        await session.commit()

        # Phase 3: embed + write back via UPDATE (no held connection during I/O).
        actually_embedded = 0
        for start in range(0, len(to_embed), self._batch_size):
            batch = to_embed[start : start + self._batch_size]

            # Defensive: chunks may have been deleted between Phase 1 and now.
            batch_chunk_ids = [cid for cid, _, _ in batch]
            existing_rows = await session.execute(
                select(MdChunk.id).where(MdChunk.id.in_(batch_chunk_ids))
            )
            existing_ids = {row[0] for row in existing_rows.all()}
            await session.commit()
            batch = [
                (cid, text, hsh) for cid, text, hsh in batch if cid in existing_ids
            ]

            if not batch:
                continue

            vectors = await self._provider.embed([text for _, text, _ in batch])

            for (chunk_id, _, computed_hash), vector in zip(
                batch, vectors, strict=True
            ):
                await session.execute(
                    update(MdChunk)
                    .where(MdChunk.id == chunk_id)
                    .values(
                        embedding=vector,
                        model=self._provider.model,
                        content_hash=computed_hash,
                    )
                )
            await session.commit()
            actually_embedded += len(batch)

            current_item: str | None = None
            if chunk_sources:
                last_chunk_id = batch[-1][0]
                current_item = chunk_sources.get(last_chunk_id)

            if progress_callback:
                await progress_callback(
                    actually_embedded,
                    len(to_embed),
                    current_item,
                )

        return EmbedResult(
            embedded_nodes=actually_embedded,
            skipped_nodes=skipped,
            model=self._provider.model,
        )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _chunk_text(chunk: MdChunk) -> str:
    parts: list[str] = []
    if chunk.heading_path:
        parts.append(" > ".join(chunk.heading_path))
    parts.append(chunk.content[:4096])
    return "\n".join(parts)[:4096]
