"""Fixtures for integration tests that require a live PostgreSQL instance.

Tests in this package are skipped automatically when PostgreSQL is not
reachable at TEST_DATABASE_URL (default: localhost:5432/cograph_test).
Mark tests with ``@pytest.mark.integration`` so they can be selected or
excluded via ``-m integration / -m 'not integration'``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import backend.app.models  # noqa: F401 — registers all ORM models
from backend.app.db.base import Base

_BACKEND_DIR = Path(__file__).resolve().parents[2]

def _run_alembic_upgrade(async_url: str) -> None:
    # env.py calls asyncio.run() at import time, which collides with the
    # pytest-asyncio event loop already driving this fixture — shell out.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/alembic.ini",
            "upgrade",
            "head",
        ],
        check=True,
        env={**os.environ, "COGRAPH_DATABASE__URL": async_url},
    )

_PG_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/cograph_test",
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg_engine():
    # NullPool keeps connections per-task: avoids "Future attached to a
    # different loop" when pytest-asyncio creates a fresh loop per function
    # while the engine itself lives at module scope.
    engine = create_async_engine(_PG_URL, echo=False, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("PostgreSQL not available — set TEST_DATABASE_URL to run integration tests")
        return

    async with engine.begin() as conn:
        # Wipe public schema so alembic re-applies every migration. drop_all
        # alone leaves alembic_version, making the next upgrade a no-op.
        await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    if _PG_URL.startswith("postgresql"):
        # Alembic runs BM25 tsvector + GIN index migrations that create_all skips
        _run_alembic_upgrade(_PG_URL)
    else:
        # SQLite: BM25 tsvector columns and GIN indexes are not exercised
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def pg_session(pg_engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
