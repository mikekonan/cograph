"""Drop `module_embeddings` and `code_nodes.summary`.

Nothing writes or reads either one: the table never got a row, and node
summaries live in `code_node_summaries`.
"""

from __future__ import annotations

from alembic import op

revision = "0067_drop_unused_schema"
down_revision = "0066_md_collection_repo_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both drops are metadata-only but take a short exclusive lock on
    # code_nodes. Fail fast instead of queueing every reader behind a
    # running sync; re-run when the cluster is idle.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table("module_embeddings")
    op.drop_column("code_nodes", "summary")


def downgrade() -> None:
    raise NotImplementedError(
        "0067_drop_unused_schema is a one-way drop of unused schema. "
        "Restore from backup if needed."
    )
