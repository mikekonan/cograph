"""MdLinkResolver — resolves cross-document links after upload.

Maps href values to target_document_id by matching against source_key
or normalized path within the same collection.
"""
from __future__ import annotations

from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.md_collection import MdDocument, MdLink


class MdLinkResolver:
    async def resolve_collection(
        self,
        *,
        session: AsyncSession,
        collection_id: UUID,
        progress_callback: Callable[[int, int, str | None], Awaitable[object]] | None = None,
    ) -> int:
        """Resolve all unresolved links in a collection. Returns resolved count."""
        unresolved = list(
            (
                await session.scalars(
                    select(MdLink)
                    .join(MdDocument, MdLink.source_document_id == MdDocument.id)
                    .where(
                        MdDocument.collection_id == collection_id,
                        MdLink.target_document_id.is_(None),
                    )
                )
            ).all()
        )

        if not unresolved:
            return 0

        # Build a lookup from normalized href -> document_id
        documents = list(
            (
                await session.scalars(
                    select(MdDocument).where(
                        MdDocument.collection_id == collection_id
                    )
                )
            ).all()
        )
        lookup: dict[str, UUID] = {}
        for doc in documents:
            lookup[doc.source_key] = doc.id
            # Also index by basename for wiki-style references
            basename = doc.source_key.split("/")[-1]
            lookup[basename] = doc.id
            if basename.endswith(".md"):
                lookup[basename[:-3]] = doc.id

        resolved = 0
        for idx, link in enumerate(unresolved):
            target_id = self._resolve_href(link.href, lookup)
            if target_id is not None:
                await session.execute(
                    update(MdLink)
                    .where(MdLink.id == link.id)
                    .values(target_document_id=target_id)
                )
                resolved += 1
            if progress_callback and (idx + 1) % 50 == 0:
                await progress_callback(idx + 1, len(unresolved), link.href)

        await session.commit()
        if progress_callback:
            await progress_callback(len(unresolved), len(unresolved), None)
        return resolved

    @staticmethod
    def _resolve_href(href: str, lookup: dict[str, UUID]) -> UUID | None:
        # Direct match
        if href in lookup:
            return lookup[href]

        # Normalize: strip leading ./ and trailing .md
        normalized = href.lstrip("./")
        if normalized in lookup:
            return lookup[normalized]

        if normalized.endswith(".md"):
            without_ext = normalized[:-3]
            if without_ext in lookup:
                return lookup[without_ext]

        # Try basename only
        basename = normalized.split("/")[-1]
        if basename in lookup:
            return lookup[basename]

        if basename.endswith(".md"):
            without_ext = basename[:-3]
            if without_ext in lookup:
                return lookup[without_ext]

        return None
