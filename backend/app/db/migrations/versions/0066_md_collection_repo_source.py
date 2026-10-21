"""Let a markdown collection mirror a folder of an indexed repository.

Revision ID: 0066_md_collection_repo_source
Revises: 0065_code_nodes_name_idx

A collection bound to (repository, folder) is refreshed by that repository's
sync: the folder's markdown is upserted into the collection, files gone from
the folder are pruned, and the folder is no longer indexed as repo docs.

SET NULL on the FK because repository purge ends with a bare
`DELETE FROM repositories`; an unbound collection stays as a frozen snapshot.

Additive, nullable columns - metadata-only on PG11+ (no table rewrite).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0066_md_collection_repo_source"
down_revision = "0065_code_nodes_name_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "md_collections",
        sa.Column("source_repository_id", sa.Uuid(), nullable=True),
    )
    op.add_column("md_collections", sa.Column("source_path", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_md_collections_source_repository_id_repositories",
        "md_collections",
        "repositories",
        ["source_repository_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_md_collections_source_repository_id_repositories",
        "md_collections",
        type_="foreignkey",
    )
    op.drop_column("md_collections", "source_path")
    op.drop_column("md_collections", "source_repository_id")
