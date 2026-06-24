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
   bump plan/run metadata. Existing `quality` is upgraded if the new
   status is strictly better (or the old one unknown) — a degraded page
   that healed with identical bytes must stop being dirty — but is never
   regressed by a same-content rerun with worse telemetry.
5. Otherwise → write the row (insert or full update).

After all upserts, the orchestrator calls `delete_orphan_pages` with a
`keep_slugs` set that includes both successfully resolved pages AND
slugs that failed transiently in Stage 4 — see `pipeline.py`.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.wiki.schemas import QualityStatus, ResolvedPage

logger = logging.getLogger(__name__)


_QUALITY_RANK: dict[QualityStatus, int] = {
    QualityStatus.OK: 2,
    QualityStatus.PARTIAL: 1,
    QualityStatus.DEGRADED: 0,
}


def _next_edit_streak(*, mode: str, existing: Document | None) -> int:
    """Consecutive cheap edits since the last full write.

    A full write (`mode="write"`) resets the counter to 0; a cheap edit
    (`mode="edit"`) increments the existing row's count. `_edit_eligible`
    in the pipeline force-rewrites a page once this reaches the configured
    cap, bounding slow prose drift across a chain of edits.
    """
    if mode == "edit":
        prev = existing.edit_streak if existing is not None else 0
        return (prev or 0) + 1
    return 0


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
        wiki_schema_version: int | None = None,
        spec_hashes_by_slug: dict[str, str] | None = None,
        cited_fingerprints_by_slug: dict[str, str] | None = None,
    ) -> tuple[list[UUID], list[str], list[str]]:
        """Upsert pages keyed on `(repository_id, slug)`.

        The three incremental-reuse stamps (`spec_hash`,
        `cited_fingerprint`, `wiki_schema_version`) are written on the
        full-write and content-hash-skip paths but NOT on the quality-keep
        path: a kept row still holds the *old* content and citations, so its
        old fingerprint must keep marking it dirty until a better rewrite
        lands.

        Returns:
            `(persisted_document_ids, skipped_slugs, kept_for_quality_slugs)` —
            `skipped_slugs` are pages whose `content_hash` matched the
            existing row; `kept_for_quality_slugs` are pages whose new
            quality would have regressed below the persisted quality and
            whose content was therefore preserved.
        """
        if not pages:
            return [], [], []
        spec_hashes = spec_hashes_by_slug or {}
        cited_fingerprints = cited_fingerprints_by_slug or {}

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
            # metadata. `quality` is upgraded only when the new status is
            # strictly better (or the old one unknown): a degraded page
            # whose rewrite reproduced identical bytes has *healed* — not
            # recording that would keep it dirty (and rewritten) on every
            # subsequent sync. Equal-or-worse telemetry must not overwrite
            # the recorded quality.
            if existing is not None and existing.content_hash == content_hash:
                skipped_slugs.append(page.slug)
                existing.sync_run_id = sync_run_id
                existing.source_commit = source_commit
                existing.source_hash = source_hash
                existing.sort_order = page.sort_order
                existing.parent_slug = page.parent_slug
                existing.spec_hash = spec_hashes.get(page.slug)
                existing.cited_fingerprint = cited_fingerprints.get(page.slug)
                existing.wiki_schema_version = wiki_schema_version
                existing.content_src = page.content_src
                existing.cited_content_hashes = page.cited_content_hashes
                existing.edit_streak = _next_edit_streak(
                    mode=page.mode, existing=existing
                )
                if (
                    existing_status is None
                    or _QUALITY_RANK[new_status] > _QUALITY_RANK[existing_status]
                ):
                    existing.quality = quality_payload
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
                    spec_hash=spec_hashes.get(page.slug),
                    cited_fingerprint=cited_fingerprints.get(page.slug),
                    wiki_schema_version=wiki_schema_version,
                    content_src=page.content_src,
                    cited_content_hashes=page.cited_content_hashes,
                    edit_streak=_next_edit_streak(mode=page.mode, existing=None),
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
                existing.spec_hash = spec_hashes.get(page.slug)
                existing.cited_fingerprint = cited_fingerprints.get(page.slug)
                existing.wiki_schema_version = wiki_schema_version
                existing.content_src = page.content_src
                existing.cited_content_hashes = page.cited_content_hashes
                existing.edit_streak = _next_edit_streak(
                    mode=page.mode, existing=existing
                )
                await session.flush()
                persisted_ids.append(existing.id)

        return persisted_ids, skipped_slugs, kept_for_quality_slugs

    async def touch_pages(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        slugs: list[str],
        sync_run_id: UUID | None,
        source_commit: str,
        cited_fingerprints: dict[str, str] | None = None,
    ) -> int:
        """Bump audit fields on clean (skipped) pages without touching
        content or quality. Returns updated count.

        The incremental orchestrator calls this for pages it decided not
        to rewrite, so `source_commit` reflects the sync that last
        *verified* the page — not the one that last wrote it.

        `cited_fingerprints` is the lazy-floor "adopt" set: clean pages whose
        stored `cited_fingerprint` was NULL get the freshly computed value
        stamped here (a one-time per-page UPDATE, only on the syncs right
        after a deploy / for un-backfilled rows). The page is NOT rewritten —
        the stamp just records the evidence the already-current body cited.
        """
        if not slugs:
            return 0
        stmt = (
            update(Document)
            .where(
                Document.repository_id == repository_id,
                Document.doc_type == self.DOC_TYPE,
                Document.slug.in_(slugs),
            )
            .values(sync_run_id=sync_run_id, source_commit=source_commit)
        )
        result = await session.execute(stmt)
        for slug, fingerprint in (cited_fingerprints or {}).items():
            await session.execute(
                update(Document)
                .where(
                    Document.repository_id == repository_id,
                    Document.doc_type == self.DOC_TYPE,
                    Document.slug == slug,
                )
                .values(cited_fingerprint=fingerprint)
            )
        return int(result.rowcount or 0)

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
