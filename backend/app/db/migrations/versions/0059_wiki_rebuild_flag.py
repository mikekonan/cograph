"""OWNER-requested full wiki rebuild flag on sync runs.

Revision ID: 0059_wiki_rebuild_flag
Revises: 0058_wiki_artifacts

`repo_sync_runs.wiki_rebuild_requested` — set by the reindex API when an
OWNER asks for a from-scratch wiki regeneration; the sync processor reads
it and runs the wiki stage with `force_full=True` (no artifact reuse, no
dirty-set skipping). A dedicated column beats threading a new positional
argument through the arq job payload.

NB: revision IDs live in `alembic_version.version_num VARCHAR(32)` —
`0059_wiki_rebuild_flag` is 22 chars, under the cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_wiki_rebuild_flag"
down_revision = "0058_wiki_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repo_sync_runs",
        sa.Column(
            "wiki_rebuild_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("repo_sync_runs", "wiki_rebuild_requested")
