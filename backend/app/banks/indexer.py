from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.bank import BankDocument, BankDocumentChunk
from backend.app.models.enums import BankDocumentSourceKind
from backend.app.repo_docs.chunker import RepoDocumentChunker


@dataclass(slots=True, kw_only=True)
class BankDocumentUpsertInput:
    source_key: str
    content: str
    title: str | None = None
    filename: str | None = None
    source_kind: BankDocumentSourceKind = BankDocumentSourceKind.UPLOAD
    external_id: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(slots=True, kw_only=True)
class BankDocumentUpsertResult:
    id: UUID
    bank_id: UUID
    title: str
    source_kind: BankDocumentSourceKind
    source_key: str
    external_id: str | None
    bytes: int
    chunk_count: int
    created_at: object
    updated_at: object
    created: bool
    replaced: bool
    unchanged: bool


@dataclass(slots=True, kw_only=True)
class BankDocumentBatchResult:
    items: list[BankDocumentUpsertResult]
    indexed_documents: int
    indexed_chunks: int
    unchanged_documents: int


class BankIndexer:
    def __init__(
        self,
        *,
        chunker: RepoDocumentChunker | None = None,
    ) -> None:
        self._chunker = chunker or RepoDocumentChunker()

    async def upsert_document(
        self,
        *,
        session: AsyncSession,
        bank_id: UUID,
        document: BankDocumentUpsertInput,
    ) -> BankDocumentUpsertResult:
        result = await self.upsert_documents(
            session=session,
            bank_id=bank_id,
            documents=[document],
        )
        return result.items[0]

    async def upsert_documents(
        self,
        *,
        session: AsyncSession,
        bank_id: UUID,
        documents: list[BankDocumentUpsertInput],
    ) -> BankDocumentBatchResult:
        items: list[BankDocumentUpsertResult] = []
        indexed_chunks = 0
        unchanged_documents = 0

        for document_input in documents:
            normalized_source_key = document_input.source_key.strip()
            content_hash = _content_hash(document_input.content)
            existing_document = await session.scalar(
                select(BankDocument).where(
                    BankDocument.bank_id == bank_id,
                    BankDocument.source_kind == document_input.source_kind,
                    BankDocument.source_key == normalized_source_key,
                )
            )
            content_unchanged = (
                existing_document is not None
                and existing_document.content_hash == content_hash
            )

            created = existing_document is None
            if existing_document is None:
                document = BankDocument(
                    bank_id=bank_id,
                    title="",
                    source_kind=document_input.source_kind,
                    source_key=normalized_source_key,
                    external_id=document_input.external_id,
                    content="",
                    content_hash="",
                    bytes=0,
                    document_metadata=document_input.metadata or {},
                )
                session.add(document)
                await session.flush()
            else:
                document = existing_document

            title = self._resolve_title(document_input=document_input)
            document.title = title
            document.external_id = document_input.external_id
            document.content = document_input.content
            document.content_hash = content_hash
            document.bytes = len(document_input.content.encode("utf-8"))
            document.document_metadata = document_input.metadata or {}

            replaced = existing_document is not None
            if content_unchanged:
                unchanged_documents += 1
                chunk_count = await session.scalar(
                    select(func.count())
                    .select_from(BankDocumentChunk)
                    .where(BankDocumentChunk.document_id == document.id)
                ) or 0
            else:
                chunk_count = await self._replace_document_chunks(
                    session=session,
                    document=document,
                    content=document_input.content,
                )
            indexed_chunks += chunk_count
            await session.flush()
            await session.refresh(document)

            items.append(
                BankDocumentUpsertResult(
                    id=document.id,
                    bank_id=document.bank_id,
                    title=document.title,
                    source_kind=document.source_kind,
                    source_key=document.source_key,
                    external_id=document.external_id,
                    bytes=document.bytes,
                    chunk_count=chunk_count,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                    created=created,
                    replaced=replaced,
                    unchanged=content_unchanged,
                )
            )

        await session.flush()

        return BankDocumentBatchResult(
            items=items,
            indexed_documents=len(items),
            indexed_chunks=indexed_chunks,
            unchanged_documents=unchanged_documents,
        )

    def _resolve_title(self, *, document_input: BankDocumentUpsertInput) -> str:
        if document_input.title:
            return document_input.title
        fallback_name = document_input.filename or Path(document_input.source_key).name or "Document"
        return self._chunker.extract_title(fallback_name, document_input.content) or fallback_name

    async def _replace_document_chunks(
        self,
        *,
        session: AsyncSession,
        document: BankDocument,
        content: str,
    ) -> int:
        await session.execute(
            delete(BankDocumentChunk).where(BankDocumentChunk.document_id == document.id)
        )
        chunk_drafts = self._chunker.chunk(content)
        chunk_rows = [
            BankDocumentChunk(
                document_id=document.id,
                chunk_index=chunk_draft.chunk_index,
                heading_path=chunk_draft.heading_path,
                content=chunk_draft.content,
            )
            for chunk_draft in chunk_drafts
        ]
        session.add_all(chunk_rows)
        return len(chunk_rows)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
