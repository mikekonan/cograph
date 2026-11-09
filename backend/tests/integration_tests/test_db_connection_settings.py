"""Connection settings must survive a rollback of the connection's first transaction.

They used to be `SET` in a connect hook. With asyncpg that ran inside the first
transaction, and a read-only session ends in a rollback, so Postgres undid
them: the pooled connection then ran with no statement timeout and the OS
keepalive.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.app.config import DatabaseSettings, RetrievalSettings, Settings
from backend.app.db.session import SessionManager

pytestmark = pytest.mark.integration


@pytest.mark.asyncio(loop_scope="module")
async def test_settings_survive_a_rollback_on_the_same_connection(pg_engine) -> None:
    url = pg_engine.url.render_as_string(hide_password=False)
    manager = SessionManager(
        Settings(
            database=DatabaseSettings(url=url),
            retrieval=RetrievalSettings(hnsw_iterative_scan="relaxed_order"),
        )
    )
    try:
        async with manager.session() as session:
            first_pid = (await session.execute(sa.text("select pg_backend_pid()"))).scalar_one()
            # Load pgvector in this backend: it validates hnsw.* on load.
            await session.execute(sa.text("select '[1]'::vector"))
            await session.rollback()

        async with manager.session() as session:
            pid = (await session.execute(sa.text("select pg_backend_pid()"))).scalar_one()
            assert pid == first_pid, "the pool must hand back the same connection"
            shown = [
                (await session.execute(sa.text(f"show {name}"))).scalar_one()
                for name in (
                    "statement_timeout",
                    "tcp_keepalives_idle",
                    "tcp_keepalives_interval",
                    "tcp_keepalives_count",
                    "hnsw.iterative_scan",
                )
            ]
    finally:
        await manager.dispose()

    # A unix-socket connection reports keepalives as 0; the test DB is on TCP.
    assert shown == ["5min", "60", "10", "6", "relaxed_order"]
