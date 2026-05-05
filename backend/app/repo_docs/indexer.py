from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repo_document_chunk_mention import RepoDocumentChunkMention
from backend.app.repo_docs.chunker import RepoDocumentChunkDraft, RepoDocumentChunker
from backend.app.repo_docs.discover import RepoDocumentDiscoverer, RepoDocumentKind, classify_repo_document
from backend.app.repo_docs.symbol_linker import RepoDocumentSymbolLinker


@dataclass(slots=True, kw_only=True)
class RepoDocumentIndexResult:
    discovered_files: int
    indexed_documents: int
    indexed_chunks: int
    unchanged_documents: int
    deleted_documents: int
    replaced_files: tuple[str, ...]


class RepoDocumentIndexer:
    def __init__(
        self,
        *,
        discoverer: RepoDocumentDiscoverer | None = None,
        chunker: RepoDocumentChunker | None = None,
        symbol_linker: RepoDocumentSymbolLinker | None = None,
    ) -> None:
        self._discoverer = discoverer or RepoDocumentDiscoverer()
        self._chunker = chunker or RepoDocumentChunker()
        self._symbol_linker = symbol_linker or RepoDocumentSymbolLinker()

    async def index_checkout(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        checkout_path: str | Path,
        relink_unchanged_documents: bool = True,
    ) -> RepoDocumentIndexResult:
        root_path = Path(checkout_path).resolve()
        document_paths = await asyncio.to_thread(self._discoverer.discover, root_path)
        relative_paths = tuple(path.relative_to(root_path).as_posix() for path in document_paths)

        existing_documents = {
            document.file_path: document
            for document in (
                await session.scalars(
                    select(RepoDocument).where(RepoDocument.repository_id == repository_id)
                )
            ).all()
        }

        deleted_paths = set(existing_documents) - set(relative_paths)
        if deleted_paths:
            await session.execute(
                delete(RepoDocument).where(
                    RepoDocument.repository_id == repository_id,
                    RepoDocument.file_path.in_(tuple(sorted(deleted_paths))),
                )
            )

        indexed_documents = 0
        indexed_chunks = 0
        unchanged_documents = 0
        replaced_files: list[str] = []

        for document_path in document_paths:
            relative_path = document_path.relative_to(root_path).as_posix()
            document_kind = classify_repo_document(relative_path)
            if document_kind is None:
                continue
            content = await asyncio.to_thread(document_path.read_text, encoding="utf-8")
            content_hash = _content_hash(content)
            existing_document = existing_documents.get(relative_path)
            if existing_document is not None and existing_document.content_hash == content_hash:
                unchanged_documents += 1
                if relink_unchanged_documents:
                    await self._relink_document_mentions(
                        session=session,
                        repository_id=repository_id,
                        document=existing_document,
                    )
                continue

            document = existing_document or RepoDocument(
                repository_id=repository_id,
                file_path=relative_path,
                title=None,
                content="",
                content_hash="",
                bytes=0,
            )
            if existing_document is None:
                session.add(document)
                await session.flush()

            document.title = self._derive_title(
                file_path=relative_path,
                content=content,
                document_kind=document_kind,
            )
            document.content = content
            document.content_hash = content_hash
            document.bytes = len(content.encode("utf-8"))

            if existing_document is not None:
                await session.execute(
                    delete(RepoDocumentChunk).where(RepoDocumentChunk.document_id == document.id)
                )

            chunk_drafts = self._chunker.chunk(content)
            # _build_chunks registers the chunks AND the mention join rows in
            # the session; no outer add_all is needed.
            chunk_rows = await self._build_chunks(
                session=session,
                repository_id=repository_id,
                document=document,
                document_kind=document_kind,
                chunk_drafts=chunk_drafts,
            )

            indexed_documents += 1
            indexed_chunks += len(chunk_rows)
            replaced_files.append(relative_path)

        await session.flush()

        return RepoDocumentIndexResult(
            discovered_files=len(document_paths),
            indexed_documents=indexed_documents,
            indexed_chunks=indexed_chunks,
            unchanged_documents=unchanged_documents,
            deleted_documents=len(deleted_paths),
            replaced_files=tuple(replaced_files),
        )

    async def _relink_document_mentions(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        document: RepoDocument,
    ) -> None:
        chunks = list(
            (
                await session.scalars(
                    select(RepoDocumentChunk)
                    .where(RepoDocumentChunk.document_id == document.id)
                    .order_by(RepoDocumentChunk.chunk_index.asc())
                )
            ).all()
        )
        for chunk in chunks:
            mentions = await self._symbol_linker.link_chunk_mentions(
                session=session,
                repository_id=repository_id,
                document_file_path=document.file_path,
                chunk_content=chunk.content,
            )
            chunk.mentions = [str(mention.node_id) for mention in mentions]
            # Dual-write: populate the normalized join table alongside the
            # legacy UUID array so 0008_finalize can drop `chunk.mentions`
            # without losing data. Wiping first keeps the set idempotent.
            await session.execute(
                delete(RepoDocumentChunkMention).where(
                    RepoDocumentChunkMention.chunk_id == chunk.id,
                )
            )
            seen_node_ids: set[UUID] = set()
            for mention in mentions:
                if mention.node_id in seen_node_ids:
                    continue
                seen_node_ids.add(mention.node_id)
                session.add(
                    RepoDocumentChunkMention(
                        chunk_id=chunk.id,
                        code_node_id=mention.node_id,
                    )
                )

    async def _build_chunks(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        document: RepoDocument,
        document_kind: RepoDocumentKind,
        chunk_drafts: list[RepoDocumentChunkDraft],
    ) -> list[RepoDocumentChunk]:
        chunk_rows: list[RepoDocumentChunk] = []
        pending_mentions: list[tuple[RepoDocumentChunk, list[UUID]]] = []
        for chunk_draft in chunk_drafts:
            mentions = await self._symbol_linker.link_chunk_mentions(
                session=session,
                repository_id=repository_id,
                document_file_path=document.file_path,
                chunk_content=chunk_draft.content,
            )
            mention_node_ids = [mention.node_id for mention in mentions]
            chunk = RepoDocumentChunk(
                document_id=document.id,
                chunk_index=chunk_draft.chunk_index,
                heading_path=(
                    chunk_draft.heading_path
                    if chunk_draft.heading_path
                    else [document_kind.value]
                ),
                content=chunk_draft.content,
                mentions=[str(node_id) for node_id in mention_node_ids],
            )
            chunk_rows.append(chunk)
            pending_mentions.append((chunk, mention_node_ids))

        if pending_mentions:
            # Chunks need primary keys before we can attach mention rows.
            session.add_all(chunk_rows)
            await session.flush()
            for chunk, mention_node_ids in pending_mentions:
                # Deduplicate: two different raw symbol names can resolve to the
                # same CodeNode, which would violate pk_repo_document_chunk_mentions.
                seen_node_ids: set[UUID] = set()
                for node_id in mention_node_ids:
                    if node_id in seen_node_ids:
                        continue
                    seen_node_ids.add(node_id)
                    session.add(
                        RepoDocumentChunkMention(
                            chunk_id=chunk.id,
                            code_node_id=node_id,
                        )
                    )
        return chunk_rows

    def _derive_title(
        self,
        *,
        file_path: str,
        content: str,
        document_kind: RepoDocumentKind,
    ) -> str:
        title = self._chunker.extract_title(file_path, content)
        file_name = file_path.rsplit("/", 1)[-1]
        if document_kind is RepoDocumentKind.REPO_DOC or title != file_name:
            return title
        return f"{document_kind.value}: {file_name}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
