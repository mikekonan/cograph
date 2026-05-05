"""Drop legacy wiki schema after the wiki-llm-v1 rip-and-replace.

Revision ID: 0028_drop_legacy_wiki_schema
Revises: 0027_add_repo_catalogs_table

The LLM-driven wiki pipeline writes a single variant of generated
pages with no `page_kind` / `section_kind` taxonomy and no rollout layer. This
migration drops the now-unused columns and tables. The legacy `documents`
rows produced by V1/V2/V3 are discarded - there is no data
to preserve.
"""

from __future__ import annotations

from alembic import op


revision = "0028_drop_legacy_wiki_schema"
down_revision = "0027_add_repo_catalogs_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Wipe legacy wiki rows so the column drop and the new pipeline start
    # from a clean slate. doc_type='wiki' matches both legacy V1 ('default'
    # variant) and any orphan preview rows; rows from other product surfaces
    # (none currently) would have a different doc_type.
    op.execute("DELETE FROM documents")

    op.drop_index("idx_documents_repository_variant_sort", table_name="documents")
    op.drop_column("documents", "generation_version")
    op.drop_column("documents", "variant")
    op.drop_column("documents", "section_kind")
    op.drop_column("documents", "page_kind")

    op.drop_column("repositories", "wiki_default_variant")

    op.drop_index("idx_repo_catalogs_repo", table_name="repo_catalogs")
    op.drop_table("repo_catalogs")

    op.drop_index("idx_evidence_packs_repo_sort", table_name="evidence_packs")
    op.drop_index("idx_evidence_packs_repo_kind", table_name="evidence_packs")
    op.drop_table("evidence_packs")


def downgrade() -> None:
    raise NotImplementedError(
        "0028_drop_legacy_wiki_schema is a one-way drop — the wiki-llm-v1 "
        "pipeline does not write the dropped columns/tables, so a downgrade "
        "would leave the schema half-populated. Restore from backup if needed."
    )
