"""Drop banks subsystem (tables + dangling FKs).

Revision ID: 0045_drop_banks
Revises: 0044_widen_reasoning_effort

Banks were the original LLM-fact-extraction layer built for a chat surface
that never shipped. The user-facing path (Web UI / REST / MCP) is
``md_collections``; banks had no FE, no MCP exposure, and the per-document
LLM extraction was prohibitively expensive at the 2k-document scale we
target. Tables, columns, sync-batch kinds, and pipeline steps for banks
are dropped here as a single hard cut. Cograph has zero installs in the
wild, so no compat shim, no data migration.

Dropped:

* ``bank_observations``
* ``bank_facts``
* ``bank_entities``
* ``bank_document_chunks``
* ``bank_documents``
* ``banks``
* ``sync_jobs.bank_id``
* ``sync_batches.bank_id``
"""

from __future__ import annotations

from alembic import op


revision = "0045_drop_banks"
down_revision = "0044_widen_reasoning_effort"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Concurrent indexes from 0021 (which were created CONCURRENTLY in a
    # NOT IN TRANSACTION block) get dropped along with their tables.
    op.execute("DROP INDEX IF EXISTS idx_bank_facts_tsv")
    op.execute("DROP INDEX IF EXISTS idx_bank_facts_hnsw")

    op.drop_table("bank_observations")
    op.drop_table("bank_facts")
    op.drop_table("bank_entities")
    op.drop_table("bank_document_chunks")
    op.drop_table("bank_documents")
    op.drop_table("banks")

    with op.batch_alter_table("sync_jobs") as batch:
        batch.drop_column("bank_id")
    with op.batch_alter_table("sync_batches") as batch:
        batch.drop_column("bank_id")


def downgrade() -> None:
    raise NotImplementedError(
        "0045_drop_banks is irreversible; banks were removed wholesale."
    )
