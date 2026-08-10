"""Extend code_nodes with byte ranges, symbol_key, summary, role.

Nullable columns during co-existence phase — the legacy writer still
populates ``content``, ``callers``, ``callees``; the new writer fills
``source_file_id``, ``start_byte``, ``end_byte``, ``symbol_key`` and
role heuristically. ``summary`` stays NULL until the background
summary-generation job produces an LLM description.

Also extends the ``node_type`` CHECK to cover variables, constants,
type aliases, and class attributes. The existing CHECK (if any) under
``ck_code_nodes_node_type`` is replaced.

Adds ``module_embeddings`` table. One row per module node carrying an
aggregated embedding — used by the architectural retrieval path to
seed queries at module granularity before drilling into symbols.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_code_nodes_ranges"
down_revision = "0005_source_files"
branch_labels = None
depends_on = None


NODE_TYPE_VALUES = (
    "module",
    "class",
    "struct",
    "interface",
    "function",
    "method",
    "variable",
    "constant",
    "type_alias",
    "attribute",
)

ROLE_VALUES = (
    "entry_point",
    "service",
    "repository",
    "model",
    "helper",
    "config",
    "test",
    "constant",
    "type_alias",
    "attribute",
    "other",
)


def upgrade() -> None:
    op.execute("ALTER TABLE code_nodes DROP CONSTRAINT IF EXISTS ck_code_nodes_node_type")
    node_type_list = ", ".join(f"'{value}'" for value in NODE_TYPE_VALUES)
    op.create_check_constraint(
        "ck_code_nodes_node_type",
        "code_nodes",
        f"node_type IN ({node_type_list})",
    )

    op.add_column(
        "code_nodes",
        sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("code_nodes", sa.Column("start_byte", sa.Integer(), nullable=True))
    op.add_column("code_nodes", sa.Column("end_byte", sa.Integer(), nullable=True))
    op.add_column("code_nodes", sa.Column("symbol_key", sa.Text(), nullable=True))
    op.add_column("code_nodes", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("code_nodes", sa.Column("role", sa.Text(), nullable=True))

    op.create_foreign_key(
        "fk_code_nodes_source_file_id_source_files",
        "code_nodes",
        "source_files",
        ["source_file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_code_nodes_byte_range",
        "code_nodes",
        "start_byte IS NULL OR (start_byte >= 0 AND end_byte > start_byte)",
    )
    role_list = ", ".join(f"'{value}'" for value in ROLE_VALUES)
    op.create_check_constraint(
        "ck_code_nodes_role",
        "code_nodes",
        f"role IS NULL OR role IN ({role_list})",
    )

    op.create_index(
        "idx_code_nodes_source_file",
        "code_nodes",
        ["repository_id", "source_file_id"],
        unique=False,
    )
    op.create_index(
        "idx_code_nodes_symbol_key",
        "code_nodes",
        ["repository_id", "symbol_key"],
        unique=False,
    )
    op.create_index(
        "idx_code_nodes_role",
        "code_nodes",
        ["repository_id", "role"],
        unique=False,
        postgresql_where=sa.text("role IS NOT NULL"),
    )

    op.create_table(
        "module_embeddings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "embedding",
            postgresql.BYTEA(),
            nullable=True,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["module_node_id"],
            ["code_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "repository_id",
            "module_node_id",
            name="uq_module_embeddings_repo_module",
        ),
    )
    # HNSW index on the embedding vector is added in a follow-up migration
    # once pgvector is wired (``vector(N)`` type). Placeholder BYTEA keeps the
    # schema migratable without the extension present in dev.


def downgrade() -> None:
    # Refuse to downgrade if any v2-only node_type is present. The old CHECK
    # constraint would reject such rows at create-time and abort the whole
    # migration mid-way. Explicit failure here is safer than silent corruption:
    # the operator must decide whether to delete those rows or keep v2.
    bind = op.get_bind()
    v2_only_values = ("variable", "constant", "type_alias", "attribute")
    placeholders = ", ".join(f"'{value}'" for value in v2_only_values)
    v2_row_count = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM code_nodes WHERE node_type IN ({placeholders})"
        )
    ).scalar_one()
    if v2_row_count:
        raise RuntimeError(
            "0006 downgrade refused: "
            f"{v2_row_count} code_nodes rows have v2-only node_type values "
            f"({', '.join(v2_only_values)}). Delete or remap them before "
            "downgrading."
        )

    op.drop_table("module_embeddings")

    op.drop_index("idx_code_nodes_role", table_name="code_nodes")
    op.drop_index("idx_code_nodes_symbol_key", table_name="code_nodes")
    op.drop_index("idx_code_nodes_source_file", table_name="code_nodes")

    op.drop_constraint("ck_code_nodes_role", "code_nodes", type_="check")
    op.drop_constraint("ck_code_nodes_byte_range", "code_nodes", type_="check")
    op.drop_constraint(
        "fk_code_nodes_source_file_id_source_files",
        "code_nodes",
        type_="foreignkey",
    )

    op.drop_column("code_nodes", "role")
    op.drop_column("code_nodes", "summary")
    op.drop_column("code_nodes", "symbol_key")
    op.drop_column("code_nodes", "end_byte")
    op.drop_column("code_nodes", "start_byte")
    op.drop_column("code_nodes", "source_file_id")

    op.drop_constraint("ck_code_nodes_node_type", "code_nodes", type_="check")
    old_values = ("module", "class", "struct", "interface", "function", "method")
    old_list = ", ".join(f"'{value}'" for value in old_values)
    op.create_check_constraint(
        "ck_code_nodes_node_type",
        "code_nodes",
        f"node_type IN ({old_list})",
    )
