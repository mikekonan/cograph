"""query_logs.path_prefix: the folder a cograph_search_code call was limited to.

Revision ID: 0071_query_log_path_prefix
Revises: 0070_code_tsv_dotted_parts

Without it an empty answer under a folder looked like a search that missed a
name the repository has. Nullable, no backfill; adding it rewrites nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0071_query_log_path_prefix"
down_revision = "0070_code_tsv_dotted_parts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A long read holds the table: give up rather than queue every log write
    # behind the waiting ALTER.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column("query_logs", sa.Column("path_prefix", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("query_logs", "path_prefix")
