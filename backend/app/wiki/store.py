"""Stage 6: persistence into the `documents` table.

Reuses the existing `Document` model (mig 0019). New rows always have
`doc_type='wiki'`. The legacy `page_kind`, `section_kind`, `variant`, and
`generation_version` columns were dropped in migration 0028.

Per-page persist decision (in priority order):

1. No existing row → insert (first time wins regardless of quality).
2. Existing row, but its recorded `quality.quality_status` is missing,
   NULL, or unparseable → treat as unknown; write the new row.
3. New page's `quality_status` is strictly worse than the existing row's
   (rank: ok=2, partial=1, degraded=0) → keep the existing row's content,
   citations, source ids, and `quality`. Only `sync_run_id` and
   `source_commit` are bumped (audit trail). Slug is reported in the
   returned `kept_for_quality_slugs`.
4. New page's `content_hash` matches the existing row → skip body write,
   bump plan/run metadata. Existing `quality` is **left intact** — a
   same-content rerun must not silently regress recorded quality.
5. Otherwise → write the row (insert or full update).

After all upserts, the orchestrator calls `delete_orphan_pages` with a
`keep_slugs` set that includes both successfully resolved pages AND
slugs that failed transiently in Stage 4 — see `pipeline.py`.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.wiki.schemas import QualityStatus, ResolvedPage

logger = logging.getLogger(__name__)


_QUALITY_RANK: dict[QualityStatus, int] = {
    QualityStatus.OK: 2,
    QualityStatus.PARTIAL: 1,
    QualityStatus.DEGRADED: 0,
}


def _existing_quality_status(quality: object) -> QualityStatus | None:
    """Extract `quality_status` from a persisted `documents.quality` JSON.

    Returns `None` for NULL, missing-key, or unparseable values; callers
    treat that as "unknown" and let the new row through.
    """
    if not isinstance(quality, dict):
        return None
    raw = quality.get("quality_status")
    if not isinstance(raw, str):
        return None
    try:
        return QualityStatus(raw)
    except ValueError:
        return None


class WikiDocumentStore:
    """Upsert wiki pages into `documents` and clean up orphan slugs."""

    DOC_TYPE = "wiki"

    async def upsert_pages(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        sync_run_id: UUID | None,
        source_commit: str,
        plan_hash: str,
        model: str,
        pages: list[ResolvedPage],
    ) -> tuple[list[UUID], list[str], list[str]]:
        """Upsert pages keyed on `(repository_id, slug)`.

        Returns:
            `(persisted_document_ids, skipped_slugs, kept_for_quality_slugs)` —
            `skipped_slugs` are pages whose `content_hash` matched the
            existing row; `kept_for_quality_slugs` are pages whose new
            quality would have regressed below the persisted quality and
            whose content was therefore preserved.
        """
        if not pages:
            return [], [], []

        slugs = [page.slug for page in pages]
        existing_stmt = select(Document).where(
            Document.repository_id == repository_id,
            Document.doc_type == self.DOC_TYPE,
            Document.slug.in_(slugs),
        )
        existing_rows = (await session.execute(existing_stmt)).scalars().all()
        existing_by_slug: dict[str, Document] = {row.slug: row for row in existing_rows}

        persisted_ids: list[UUID] = []
        skipped_slugs: list[str] = []
        kept_for_quality_slugs: list[str] = []

        for page in pages:
            content_hash = hashlib.sha256(page.content.encode("utf-8")).hexdigest()
            source_hash = _source_hash(
                source_commit=source_commit, plan_hash=plan_hash, slug=page.slug
            )
            citations_payload = [c.model_dump(mode="json") for c in page.citations]
            source_node_ids = [str(nid) for nid in page.source_node_ids]
            source_repo_doc_chunk_ids = [
                str(cid) for cid in page.source_repo_doc_chunk_ids
            ]
            quality_payload = page.quality.model_dump(mode="json")

            existing = existing_by_slug.get(page.slug)
            existing_status = (
                _existing_quality_status(existing.quality)
                if existing is not None
                else None
            )
            new_status = page.quality.quality_status

            # Decision 3 — new run would regress per-page quality. Keep the
            # existing row's content + recorded quality; bump only audit
            # fields so we know the latest run touched the row.
            if (
                existing is not None
                and existing_status is not None
                and _QUALITY_RANK[new_status] < _QUALITY_RANK[existing_status]
            ):
                existing.sync_run_id = sync_run_id
                existing.source_commit = source_commit
                kept_for_quality_slugs.append(page.slug)
                persisted_ids.append(existing.id)
                continue

            # Decision 4 — same content as before. Refresh plan/run
            # metadata but leave `quality` alone: a same-content rerun
            # with worse telemetry must not regress recorded quality.
            if existing is not None and existing.content_hash == content_hash:
                skipped_slugs.append(page.slug)
                existing.sync_run_id = sync_run_id
                existing.source_commit = source_commit
                existing.source_hash = source_hash
                existing.sort_order = page.sort_order
                existing.parent_slug = page.parent_slug
                persisted_ids.append(existing.id)
                continue

            # Decisions 1, 2, 5 — full write (new row, unknown existing
            # quality, or new quality is >= existing).
            if existing is None:
                row = Document(
                    repository_id=repository_id,
                    sync_run_id=sync_run_id,
                    slug=page.slug,
                    title=page.title,
                    doc_type=self.DOC_TYPE,
                    sort_order=page.sort_order,
                    parent_slug=page.parent_slug,
                    source_commit=source_commit,
                    content=page.content,
                    content_hash=content_hash,
                    source_hash=source_hash,
                    model=model,
                    source_node_ids=source_node_ids,
                    source_repo_doc_chunk_ids=source_repo_doc_chunk_ids,
                    citations=citations_payload,
                    quality=quality_payload,
                )
                session.add(row)
                await session.flush()
                persisted_ids.append(row.id)
            else:
                existing.title = page.title
                existing.sync_run_id = sync_run_id
                existing.sort_order = page.sort_order
                existing.parent_slug = page.parent_slug
                existing.source_commit = source_commit
                existing.content = page.content
                existing.content_hash = content_hash
                existing.source_hash = source_hash
                existing.model = model
                existing.source_node_ids = source_node_ids
                existing.source_repo_doc_chunk_ids = source_repo_doc_chunk_ids
                existing.citations = citations_payload
                existing.quality = quality_payload
                await session.flush()
                persisted_ids.append(existing.id)

        return persisted_ids, skipped_slugs, kept_for_quality_slugs

    async def delete_orphan_pages(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        keep_slugs: list[str],
    ) -> int:
        """Delete `documents` rows for this repo whose slug is not in `keep_slugs`
        and whose `doc_type='wiki'`. Returns deleted count.
        """
        keep = list(set(keep_slugs))
        stmt = delete(Document).where(
            Document.repository_id == repository_id,
            Document.doc_type == self.DOC_TYPE,
        )
        if keep:
            stmt = stmt.where(~Document.slug.in_(keep))
        result = await session.execute(stmt)
        return int(result.rowcount or 0)


def _source_hash(*, source_commit: str, plan_hash: str, slug: str) -> str:
    payload = f"{source_commit}:{plan_hash}:{slug}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
