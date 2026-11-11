"""query_logs: the error message and the wiki pages a search attached.

Revision ID: 0068_query_log_error_wiki
Revises: 0067_drop_unused_schema

`error_code` holds only the exception class ("ToolError"), which says
nothing about what failed. `error_msg` keeps the message, cut to 500
chars. `wiki_pages` lists the generated-wiki slugs a search result
carried, so attachment can be compared with later cograph_wiki_page
reads. Both nullable; adding them rewrites nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0068_query_log_error_wiki"
down_revision = "0067_drop_unused_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("query_logs", sa.Column("error_msg", sa.String(512), nullable=True))
    op.add_column(
        "query_logs",
        sa.Column(
            "wiki_pages", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("query_logs", "wiki_pages")
    op.drop_column("query_logs", "error_msg")
