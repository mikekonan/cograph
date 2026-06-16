"""Backfill documents.spec_hash to the narrowed reuse formula.

Revision ID: 0062_backfill_spec_hash
Revises: 0061_sync_job_cached_tok

PR1 of the disproportionate-wiki-cost fix narrows `incremental.spec_hash`
to the page *contract* — it drops the free-text planner hints `purpose`
and `sources_hint`, which the planner regenerates non-deterministically on
every re-plan and which therefore made any re-plan dirty every page (a
full-wiki rewrite triggered by planner jitter, not by a real change).

Narrowing a reuse key is a no-bump change ONLY if the existing
`documents.spec_hash` stamps are rewritten to the new formula in the same
release — otherwise every old-formula stamp reads as `spec_changed` on the
next sync and triggers the exact mass rewrite we are removing. This
migration is that rewrite.

The hash is recomputed from each repo's persisted plan
(`wiki_artifacts.plan` → `pages[]`). We import ONLY `PageSpec` (pure
pydantic — no retrieval/embedder deps reach the Alembic env) for
normalization (field defaults, the tolerant `covers_questions` validator),
and inline a FROZEN copy of the canonical-hash formula. A unit test
(`test_incremental_dirty.test_migration_0062_spec_hash_byte_identical`)
pins this inline formula byte-for-byte against `incremental.spec_hash`; if
a later PR changes that function the test goes red and that PR must ship
its own backfill migration. The inline copy must NOT be edited to track
later changes — a migration is a frozen historical artifact.

Touches `spec_hash` only — `retrieval_fingerprint`, `quality`, and
`wiki_schema_version` are left untouched so the quality-keep self-heal
signal (a degraded page re-tried every sync) survives.

Downgrade is symmetric: it recomputes the pre-PR1 formula (purpose +
sources_hint included) so an app-image rollback paired with this downgrade
does not itself spike cost. To roll back, run this downgrade BEFORE
reverting the app image.

Online-safe (per-row UPDATE keyed by the unique (repository_id, slug)).
Run with wiki workers drained so no concurrent run re-stamps mid-backfill.

NB: revision IDs live in `alembic_version.version_num VARCHAR(32)` —
`0062_backfill_spec_hash` is 23 chars, under the cap.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable

import sqlalchemy as sa
from alembic import op

from backend.app.wiki.schemas import PageSpec

revision = "0062_backfill_spec_hash"
down_revision = "0061_sync_job_cached_tok"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _spec_hash(spec: PageSpec) -> str:
    """Frozen copy of incremental.spec_hash AS OF PR1 (narrowed contract).

    Pinned byte-for-byte by a unit test — do NOT edit to track later
    changes; a future spec_hash change ships its own backfill migration.
    """
    return _canonical_hash(
        {
            "slug": spec.slug,
            "title": spec.title,
            "parent_slug": spec.parent_slug or "",
            "covers_questions": sorted(q.value for q in spec.covers_questions),
            "diagram": spec.diagram,
            "page_kind": spec.page_kind.value,
        }
    )


def _spec_hash_legacy(spec: PageSpec) -> str:
    """Frozen copy of incremental.spec_hash BEFORE PR1 (purpose +
    sources_hint included) — used only by downgrade to restore the stamps
    the pre-PR1 code would compute."""
    return _canonical_hash(
        {
            "slug": spec.slug,
            "title": spec.title,
            "parent_slug": spec.parent_slug or "",
            "purpose": spec.purpose,
            "sources_hint": sorted(spec.sources_hint),
            "covers_questions": sorted(q.value for q in spec.covers_questions),
            "diagram": spec.diagram,
            "page_kind": spec.page_kind.value,
        }
    )


def _rebackfill(hasher: Callable[[PageSpec], str], bind: object = None) -> None:
    # `bind` is a testability seam: a unit test passes a sync sqlite
    # connection directly; real runs default to the Alembic op bind.
    bind = bind if bind is not None else op.get_bind()
    artifacts = bind.execute(
        sa.text("SELECT repository_id, plan FROM wiki_artifacts")
    ).fetchall()
    updated = 0
    for repository_id, plan in artifacts:
        # JSONB comes back as a dict on Postgres; the sqlite JSON variant
        # round-trips as TEXT.
        if isinstance(plan, str):
            plan = json.loads(plan)
        if not isinstance(plan, dict):
            continue
        for page_dict in plan.get("pages", []):
            try:
                spec = PageSpec.model_validate(page_dict)
            except Exception:  # noqa: BLE001
                # One unparseable legacy page must not block the batch: it
                # keeps its old stamp, reads spec_changed next sync, and is
                # rewritten once (bounded, safe).
                logger.warning(
                    "0062: skipping unparseable page in repo %s", repository_id
                )
                continue
            result = bind.execute(
                sa.text(
                    "UPDATE documents SET spec_hash = :h "
                    "WHERE repository_id = :rid AND slug = :slug"
                ),
                {"h": hasher(spec), "rid": repository_id, "slug": spec.slug},
            )
            updated += result.rowcount or 0
    logger.info("0062: backfilled spec_hash for %s document rows", updated)


def upgrade() -> None:
    _rebackfill(_spec_hash)


def downgrade() -> None:
    _rebackfill(_spec_hash_legacy)
