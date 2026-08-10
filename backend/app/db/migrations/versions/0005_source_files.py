"""Add source_files table and repositories.graph_storage_version.

Unified raw source layer: one row per file in a repository, keyed by
(repository_id, file_path). ``code_nodes`` will begin referencing this
table and carry byte ranges so the source text lives in exactly one
place per file.

``repositories.graph_storage_version`` gates readers during per-repo
cutover: value 1 reads the legacy ``code_nodes.content``; value 2 reads
source_files via JOIN. After every repo is flipped to 2 the subsequent
migration drops the legacy column.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_source_files"
down_revision = "0004_add_banks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("raw_bytes", postgresql.BYTEA(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("blob_hash", sa.String(length=64), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "repository_id",
            "file_path",
            name="uq_source_files_repo_file_path",
        ),
        sa.CheckConstraint(
            "kind IN ('code', 'markdown', 'other')",
            name="ck_source_files_kind",
        ),
        sa.CheckConstraint("bytes >= 0", name="ck_source_files_bytes"),
    )
    op.create_index(
        "idx_source_files_repo_hash",
        "source_files",
        ["repository_id", "content_hash"],
        unique=False,
    )

    op.add_column(
        "repositories",
        sa.Column(
            "graph_storage_version",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    op.drop_column("repositories", "graph_storage_version")
    op.drop_index("idx_source_files_repo_hash", table_name="source_files")
    op.drop_table("source_files")
