"""Edit-mode columns on documents: content_src, cited_content_hashes, edit_streak.

Revision ID: 0063_wiki_edit_mode_columns
Revises: 0062_backfill_spec_hash

PR2 of the disproportionate-wiki-cost fix adds a cheap edit pass for
genuinely-minor changes (rewrite the existing page with regard to the
delta, instead of a full agentic rewrite). Three new columns on
`documents`:

* `content_src` — the raw pre-resolve page body, carrying the
  `[[node:qn]]` / `[[doc:path]]` citation placeholders. `content` is
  POST-resolve (placeholders already rendered to markdown links), so the
  editor must edit `content_src` and re-resolve. NULL on legacy /
  quality-keep rows → such a page cannot be edited and takes a full write
  until it is rewritten once (which stamps `content_src`). Not
  backfillable: doc placeholders are unrecoverable from rendered links
  (link text is the title, not the path).

* `cited_content_hashes` — `{code_node_id: content_hash}` snapshot of
  every cited node at write time. Ingest UPDATEs a changed node in place
  (same UUID, new content_hash), so the UUID-based liveness check misses
  pure body changes; this snapshot lets `compute_dirty_slugs` dirty a page
  whose cited node changed body even after it dropped out of the retrieval
  top-k, and feeds the editor the "these symbols changed" delta. NULL →
  clause skipped (legacy / pre-0063).

* `edit_streak` — consecutive cheap edits since the last full write;
  reset to 0 on a full write. Caps slow prose drift: at `edit_streak_cap`
  the page is force-rewritten from scratch.

All additive and nullable / constant-default — metadata-only on PG11+
(no table rewrite). No backfill: NULL/0 are the correct "predates
edit-mode" values and degrade safely to a full write.

NB: revision IDs live in `alembic_version.version_num VARCHAR(32)` —
`0063_wiki_edit_mode_columns` is 27 chars, under the cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_wiki_edit_mode_columns"
down_revision = "0062_backfill_spec_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_src", sa.Text(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "cited_content_hashes",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "edit_streak",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "edit_streak")
    op.drop_column("documents", "cited_content_hashes")
    op.drop_column("documents", "content_src")
