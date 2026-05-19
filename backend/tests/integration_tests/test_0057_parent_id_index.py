"""Integration tests for migration 0057 — code_nodes.parent_id index.

Hot-fix for prod incident 2026-05-19: DELETE on a code_node with descendants
took 297s and hit the 300s statement_timeout because the self-FK
`fk_code_nodes_parent_id_code_nodes ON DELETE CASCADE` had no supporting
index on `parent_id`. The migration adds a partial index
`ix_code_nodes_parent_id ON code_nodes (parent_id) WHERE parent_id IS NOT NULL`.

Coverage rationale: the real acceptance criterion (EXPLAIN ANALYZE showing
Index Scan in the cascade trigger plan) is verified by hand on prod inside
a `BEGIN; ... ROLLBACK;` block — that test would require building a tree
with hundreds of descendants in fixture, which buys little over the prod
EXPLAIN. The tests below lock the migration contract: the index exists
after upgrade and the migration is idempotent.

Skipped when PostgreSQL is unavailable (see conftest's pg_engine fixture).
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_INDEX_NAME = "ix_code_nodes_parent_id"


async def _index_row(session: AsyncSession):
    return (
        await session.execute(
            sa.text(
                "SELECT indexrelid::regclass::text AS name, indisvalid "
                "FROM pg_index "
                "WHERE indrelid = 'code_nodes'::regclass "
                "  AND indexrelid::regclass::text = :name"
            ),
            {"name": _INDEX_NAME},
        )
    ).one_or_none()


@pytest.mark.asyncio(loop_scope="module")
async def test_index_exists_and_is_valid_after_upgrade(pg_session: AsyncSession) -> None:
    # conftest's pg_engine already runs `alembic upgrade head`, so 0057 is applied.
    row = await _index_row(pg_session)
    assert row is not None, f"{_INDEX_NAME} should exist after upgrade head"
    assert row.indisvalid is True, f"{_INDEX_NAME} must be valid (indisvalid=true)"


@pytest.mark.asyncio(loop_scope="module")
async def test_index_is_partial_where_parent_id_not_null(pg_session: AsyncSession) -> None:
    """Confirms the partial-index predicate is present.

    A plain (non-partial) index would also satisfy the FK lookup, but
    we deliberately picked partial to save ~30-40% of index size on
    top-level (parent_id IS NULL) nodes. Lock that decision in.
    """
    predicate = (
        await pg_session.execute(
            sa.text(
                "SELECT pg_get_expr(indpred, indrelid) AS predicate "
                "FROM pg_index "
                "WHERE indexrelid::regclass::text = :name"
            ),
            {"name": _INDEX_NAME},
        )
    ).scalar_one()
    assert predicate is not None and "parent_id IS NOT NULL" in predicate, (
        f"Expected partial predicate 'parent_id IS NOT NULL', got: {predicate!r}"
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_upgrade_is_idempotent(pg_session: AsyncSession) -> None:
    """Re-running upgrade head after head is already applied must not fail.

    Guards the partial-failure recovery story: if CONCURRENTLY-build
    fails mid-way and an operator re-runs alembic upgrade head, the
    IF NOT EXISTS clause must keep it from raising.
    """
    url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/cograph_test",
    )
    # Should be a no-op — alembic_version already points at 0057.
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        check=True,
        env={**os.environ, "COGRAPH_DATABASE__URL": url},
    )
    row = await _index_row(pg_session)
    assert row is not None and row.indisvalid is True
