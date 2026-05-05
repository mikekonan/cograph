"""RepoDocumentEmbedderService — embeds repo_document_chunks incrementally.

Skip predicate: existing chunk has same content_hash AND same model. A model
change forces re-embedding to prevent stale cross-model vectors.
"""
from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.code_embedder import EmbedResult
from backend.app.llm.embedder import EmbedProvider
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk


class RepoDocumentEmbedderService:
    def __init__(self, provider: EmbedProvider, batch_size: int = 256) -> None:
        self._provider = provider
        self._batch_size = batch_size

    async def embed_repository(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
    ) -> EmbedResult:
        chunks = list(
            (
                await session.scalars(
                    select(RepoDocumentChunk)
                    .join(RepoDocument, RepoDocumentChunk.document_id == RepoDocument.id)
                    .where(RepoDocument.repository_id == repository_id)
                )
            ).all()
        )

        if not chunks:
            return EmbedResult(embedded_nodes=0, skipped_nodes=0, model=self._provider.model)

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
            return EmbedResult(embedded_nodes=0, skipped_nodes=skipped, model=self._provider.model)

        # Phase 2: release the DB connection before network round-trips.
        await session.commit()

        # Phase 3: embed + write back via UPDATE (no held connection during I/O).
        for start in range(0, len(to_embed), self._batch_size):
            batch = to_embed[start : start + self._batch_size]
            vectors = await self._provider.embed([text for _, text, _ in batch])

            for (chunk_id, _, computed_hash), vector in zip(batch, vectors, strict=True):
                await session.execute(
                    update(RepoDocumentChunk)
                    .where(RepoDocumentChunk.id == chunk_id)
                    .values(
                        embedding=vector,
                        model=self._provider.model,
                        content_hash=computed_hash,
                    )
                )
            await session.commit()

        return EmbedResult(
            embedded_nodes=len(to_embed),
            skipped_nodes=skipped,
            model=self._provider.model,
        )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _chunk_text(chunk: RepoDocumentChunk) -> str:
    parts: list[str] = []
    if chunk.heading_path:
        parts.append(" > ".join(chunk.heading_path))
    parts.append(chunk.content[:4096])
    return "\n".join(parts)[:4096]
