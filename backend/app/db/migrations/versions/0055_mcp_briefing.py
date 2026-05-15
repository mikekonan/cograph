"""Operator briefing for the MCP playbook.

Revision ID: 0055_mcp_briefing
Revises: 0054_repos_log_queries

NB: revision IDs are persisted in `alembic_version.version_num
VARCHAR(32)`. Keep the revision string ≤32 characters — a 34-char
form once reached prod and broke the UPDATE that records the new
head.

Singleton table — exactly one row, locked to `id=1`. Content is the
free-form markdown an operator writes at `/admin?tab=mcp` so the
MCP `instructions=` payload can carry deployment-specific guidance
(team owner, glossary, "ask me first" rules) to every connected
client at `initialize`.

`updated_by_user_id` is nullable so a system-level seed has no
attribution; once an admin saves, every subsequent PATCH stamps
their id. We deliberately do NOT keep history — the briefing is
small, plain text, and the audit trail lives in app logs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055_mcp_briefing"
down_revision = "0054_repos_log_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_operator_briefing",
        sa.Column(
            "id",
            sa.SmallInteger(),
            primary_key=True,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("id = 1", name="singleton"),
    )
    # Seed the singleton row so GET is never a 404 right after migrate.
    op.execute(
        sa.text(
            "INSERT INTO mcp_operator_briefing (id, content) VALUES (1, '') "
            "ON CONFLICT (id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_table("mcp_operator_briefing")
