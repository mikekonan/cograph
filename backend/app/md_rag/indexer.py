"""MdIndexer — idempotent upsert of markdown documents into a collection."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.md_rag.chunker import MdChunker
from backend.app.md_rag.parser import MarkdownParser, ParsedMarkdown
from backend.app.models.md_collection import MdChunk, MdDocument, MdLink
from backend.app.models.enums import MdLinkType
from backend.app.repo_docs.discover import _has_ignored_parent

logger = logging.getLogger(__name__)

MAX_MD_DOCUMENT_BYTES = 10 * 1024 * 1024
_MIRRORED_SUFFIXES = {".md", ".mdx"}


@dataclass(slots=True, kw_only=True)
class MdDocumentInput:
    source_key: str
    content: str
    title: str | None = None


@dataclass(slots=True, kw_only=True)
class MdDocumentResult:
    id: UUID
    collection_id: UUID
    source_key: str
    title: str | None
    bytes: int
    chunk_count: int
    created: bool
    replaced: bool
    unchanged: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, kw_only=True)
class MdBatchResult:
    items: list[MdDocumentResult]
    indexed_documents: int
    indexed_chunks: int
    unchanged_documents: int


class MdIndexer:
    def __init__(
        self,
        *,
        chunker: MdChunker | None = None,
        parser: MarkdownParser | None = None,
    ) -> None:
        self._chunker = chunker or MdChunker()
        self._parser = parser or MarkdownParser()

    async def upsert_document(
        self,
        *,
        session: AsyncSession,
        collection_id: UUID,
        document: MdDocumentInput,
    ) -> MdDocumentResult:
        result = await self.upsert_documents(
            session=session,
            collection_id=collection_id,
            documents=[document],
        )
        return result.items[0]

    async def upsert_documents(
        self,
        *,
        session: AsyncSession,
        collection_id: UUID,
        documents: list[MdDocumentInput],
    ) -> MdBatchResult:
        items: list[MdDocumentResult] = []
        indexed_chunks = 0
        unchanged_documents = 0

        for doc_input in documents:
            normalized_source_key = doc_input.source_key.strip()
            content_hash = _content_hash(doc_input.content)
            existing = await session.scalar(
                select(MdDocument).where(
                    MdDocument.collection_id == collection_id,
                    MdDocument.source_key == normalized_source_key,
                )
            )
            content_unchanged = (
                existing is not None and existing.content_hash == content_hash
            )
            created = existing is None
            if existing is None:
                doc = MdDocument(
                    collection_id=collection_id,
                    source_key=normalized_source_key,
                    content="",
                    content_hash="",
                    bytes=0,
                )
                session.add(doc)
                await session.flush()
            else:
                doc = existing

            parsed = self._parser.parse(doc_input.content)
            title = doc_input.title or parsed.title or normalized_source_key

            doc.title = title
            doc.content = doc_input.content
            doc.content_hash = content_hash
            doc.bytes = len(doc_input.content.encode("utf-8"))
            doc.word_count = parsed.word_count
            doc.line_count = parsed.line_count
            doc.frontmatter = parsed.frontmatter
            doc.heading_tree = parsed.heading_tree
            doc.code_blocks = parsed.code_blocks
            doc.tables = parsed.tables
            doc.links = parsed.links

            if not content_unchanged:
                from datetime import UTC, datetime
                doc.content_updated_at = datetime.now(UTC)

            replaced = existing is not None
            if content_unchanged:
                unchanged_documents += 1
                chunk_count = await session.scalar(
                    select(func.count())
                    .select_from(MdChunk)
                    .where(MdChunk.document_id == doc.id)
                ) or 0
            else:
                chunk_count = await self._replace_chunks(
                    session=session,
                    document=doc,
                    content=doc_input.content,
                )
                await self._replace_links(
                    session=session,
                    document=doc,
                    parsed=parsed,
                )
            indexed_chunks += chunk_count
            await session.flush()
            await session.refresh(doc)

            items.append(
                MdDocumentResult(
                    id=doc.id,
                    collection_id=doc.collection_id,
                    source_key=doc.source_key,
                    title=doc.title,
                    bytes=doc.bytes,
                    chunk_count=chunk_count,
                    created=created,
                    replaced=replaced,
                    unchanged=content_unchanged,
                    created_at=doc.created_at,
                    updated_at=doc.updated_at,
                )
            )

        return MdBatchResult(
            items=items,
            indexed_documents=len(items),
            indexed_chunks=indexed_chunks,
            unchanged_documents=unchanged_documents,
        )

    async def mirror_folder(
        self,
        *,
        session: AsyncSession,
        collection_id: UUID,
        checkout_root: Path,
        folder: str,
    ) -> int:
        """Make the collection match the markdown under `folder` of a checkout.

        Source keys are paths relative to `folder`. Unchanged files are
        skipped by hash; documents whose file is gone are deleted. A missing
        folder leaves the collection untouched, so a bad binding or a renamed
        folder never wipes it. Returns how many documents were written or
        deleted.
        """
        files = await asyncio.to_thread(_read_folder_markdown, checkout_root, folder)
        if files is None:
            return 0
        existing = dict(
            (
                await session.execute(
                    select(MdDocument.source_key, MdDocument.content_hash).where(
                        MdDocument.collection_id == collection_id
                    )
                )
            ).all()
        )
        changed = [
            MdDocumentInput(source_key=key, content=content)
            for key, content in files.items()
            if existing.get(key) != _content_hash(content)
        ]
        if changed:
            await self.upsert_documents(
                session=session, collection_id=collection_id, documents=changed
            )
        gone = sorted(set(existing) - set(files))
        if gone:
            await session.execute(
                delete(MdDocument).where(
                    MdDocument.collection_id == collection_id,
                    MdDocument.source_key.in_(gone),
                )
            )
        return len(changed) + len(gone)

    async def _replace_chunks(
        self,
        *,
        session: AsyncSession,
        document: MdDocument,
        content: str,
    ) -> int:
        await session.execute(
            delete(MdChunk).where(MdChunk.document_id == document.id)
        )
        drafts = self._chunker.chunk(content)
        rows = [
            MdChunk(
                document_id=document.id,
                chunk_index=draft.chunk_index,
                heading_path=draft.heading_path,
                heading_level=draft.heading_level,
                section_anchor=draft.section_anchor,
                content=draft.content,
                content_hash=_content_hash(draft.content),
            )
            for draft in drafts
        ]
        session.add_all(rows)
        return len(rows)

    async def _replace_links(
        self,
        *,
        session: AsyncSession,
        document: MdDocument,
        parsed: ParsedMarkdown,
    ) -> None:
        await session.execute(
            delete(MdLink).where(MdLink.source_document_id == document.id)
        )
        seen_hrefs: set[str] = set()
        rows = []
        for link in parsed.links:
            href = link.get("href", "")
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            link_type_str = link.get("link_type", "markdown")
            try:
                link_type = MdLinkType(link_type_str)
            except ValueError:
                link_type = MdLinkType.MARKDOWN
            rows.append(
                MdLink(
                    source_document_id=document.id,
                    link_text=link.get("text"),
                    href=href,
                    link_type=link_type,
                )
            )
        session.add_all(rows)


def _read_folder_markdown(checkout_root: Path, folder: str) -> dict[str, str] | None:
    root = checkout_root.resolve()
    base = (root / folder).resolve()
    if not base.is_dir() or not base.is_relative_to(root):
        logger.warning(
            "Mirrored folder is missing or outside the checkout; collection left as is",
            extra={"folder": folder},
        )
        return None
    files: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        if path.suffix.lower() not in _MIRRORED_SUFFIXES or not path.is_file():
            continue
        # A symlink may point outside the checkout; never read through it.
        if not path.resolve().is_relative_to(root):
            continue
        relative = PurePosixPath(path.relative_to(base).as_posix())
        if _has_ignored_parent(relative):
            continue
        if path.stat().st_size > MAX_MD_DOCUMENT_BYTES:
            logger.warning(
                "Skipping oversized markdown file in mirrored folder",
                extra={"folder": folder, "path": relative.as_posix()},
            )
            continue
        files[relative.as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return files


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
