"""Add documents.cited_fingerprint — P1 cited-only, retrieval-free reuse key.

Revision ID: 0064_wiki_cited_fingerprint
Revises: 0063_wiki_edit_mode_columns

P1 of the wiki-incremental-v2 fix replaces the whole-bundle
`retrieval_fingerprint` — which hashed the entire retrieved top-k, incl.
uncited neighbours whose ANN rank jitters on every push and dirtied pages
at zero real change — with `cited_fingerprint`: a hash of ONLY the evidence
a page actually cited, recomputable from the DB by id (no embed call).

A SEPARATE nullable column, NOT an in-place reformat of
`retrieval_fingerprint`, because:

* old and new are both 64-char sha256 (value-indistinguishable) — comparing
  a new-format value against an old-format stamp reads as drift on every
  page, i.e. the exact storm we are removing;
* the old whole-bundle hash is unreconstructable offline (it needs a
  retrieval call), so 0062's symmetric-downgrade trick is impossible here;
* rollback is then free: pre-P1 code reads the untouched
  `retrieval_fingerprint`; this downgrade just drops the new column. No
  flip-flop, idempotent in both directions.

NULL is the safe default: the runtime treats a NULL `cited_fingerprint` as
"adopt" (compute + stamp on the next sync, NOT dirty), so neither this
deploy nor a skipped/failed backfill can trigger a regeneration storm. The
eager backfill (a separate step) only accelerates recall for existing
pages — it is never a correctness dependency.

Additive, nullable column — metadata-only on PG11+ (no table rewrite),
exactly the 0063 pattern. No WIKI_SCHEMA_VERSION bump: the stored page
format is unchanged; only the reuse-key algorithm narrows (and that lands
with the runtime switch, paired with the surface-SHA edit-in-place).

NB: revision IDs live in `alembic_version.version_num VARCHAR(32)` —
`0064_wiki_cited_fingerprint` is 27 chars, under the cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0064_wiki_cited_fingerprint"
down_revision = "0063_wiki_edit_mode_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("cited_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "cited_fingerprint")
