"""Phase 7d regression tests — pg_trgm + simple-tokenizer migration round-trip.

Guards:
  - ``pg_trgm`` extension installed (by 0018) is left in place on downgrade
    (extensions are cluster-wide; we do not drop them).
  - ``code_nodes.content_tsv_simple`` GENERATED column appears on upgrade and
    is removed on downgrade.
  - GIN indexes ``idx_code_nodes_tsv_simple`` (on the simple tsvector) and
    ``idx_code_nodes_qualname_trgm`` (gin_trgm_ops on qualified_name) appear
    on upgrade and are removed on downgrade.

Run:
    COGRAPH_RUN_INTEGRATION=1 uv run pytest backend/integration_tests/test_phase7d_regression.py -q
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_migration_0018_downgrade_upgrade_clean(
    integration_database_url, integration_settings
):
    """Alembic 0018↔0017 round-trip leaves the schema in a consistent state.

    Synchronous — alembic's env.py calls asyncio.run() internally, which can't
    be nested inside a running loop.

    ``integration_settings`` is requested only to ensure the schema is at head
    before we attempt the downgrade — the fixture runs ``alembic upgrade head``
    as a side effect.
    """
    from alembic import command
    from alembic.config import Config

    _ = integration_settings  # used for its upgrade-to-head side effect

    alembic_ini = str(Path(__file__).resolve().parents[1] / "alembic.ini")
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", integration_database_url)

    async def _fetch_one(sql: str, *params) -> object:
        import asyncpg  # noqa: PLC0415

        dsn = integration_database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(sql, *params)
        finally:
            await conn.close()

    def _column_exists(table: str, column: str) -> bool:
        row = asyncio.run(
            _fetch_one(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "  AND table_name = $1 AND column_name = $2",
                table,
                column,
            )
        )
        return row is not None

    def _index_exists(name: str) -> bool:
        row = asyncio.run(
            _fetch_one(
                "SELECT 1 FROM pg_indexes "
                "WHERE schemaname = 'public' AND indexname = $1",
                name,
            )
        )
        return row is not None

    def _extension_installed(name: str) -> bool:
        row = asyncio.run(
            _fetch_one("SELECT 1 FROM pg_extension WHERE extname = $1", name)
        )
        return row is not None

    try:
        command.downgrade(cfg, "0017_ast_summaries")

        # Column and indexes must vanish on downgrade.
        assert not _column_exists("code_nodes", "content_tsv_simple"), (
            "content_tsv_simple must not exist after downgrade to 0017"
        )
        assert not _index_exists("idx_code_nodes_tsv_simple"), (
            "idx_code_nodes_tsv_simple must be dropped on downgrade"
        )
        assert not _index_exists("idx_code_nodes_qualname_trgm"), (
            "idx_code_nodes_qualname_trgm must be dropped on downgrade"
        )
        # pg_trgm extension is intentionally NOT dropped on downgrade — it's a
        # cluster-wide resource. Don't assert one way or another about presence
        # here, since it may have been pre-installed by an external init script.

    finally:
        command.upgrade(cfg, "head")

    # After upgrade — everything must be back / present.
    assert _extension_installed("pg_trgm"), (
        "pg_trgm extension must be installed after upgrade"
    )
    assert _column_exists("code_nodes", "content_tsv_simple"), (
        "content_tsv_simple column must exist after upgrade to head"
    )
    assert _index_exists("idx_code_nodes_tsv_simple"), (
        "idx_code_nodes_tsv_simple GIN index must exist after upgrade"
    )
    assert _index_exists("idx_code_nodes_qualname_trgm"), (
        "idx_code_nodes_qualname_trgm GIN-trgm index must exist after upgrade"
    )

    # Index defs must use the right opclass / strategy.
    simple_idx = asyncio.run(
        _fetch_one(
            "SELECT indexdef FROM pg_indexes WHERE indexname = $1",
            "idx_code_nodes_tsv_simple",
        )
    )
    assert simple_idx is not None
    indexdef = simple_idx["indexdef"]
    assert "USING gin" in indexdef
    assert "content_tsv_simple" in indexdef

    trgm_idx = asyncio.run(
        _fetch_one(
            "SELECT indexdef FROM pg_indexes WHERE indexname = $1",
            "idx_code_nodes_qualname_trgm",
        )
    )
    assert trgm_idx is not None
    trgm_def = trgm_idx["indexdef"]
    assert "gin_trgm_ops" in trgm_def
    assert "qualified_name" in trgm_def
