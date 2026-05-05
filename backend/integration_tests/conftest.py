from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from arq import create_pool
from sqlalchemy import text

from backend.app.config import AuthSettings, CorsSettings, DatabaseSettings, EmbeddingSettings, Environment, GitSettings, RedisSettings, Settings
from backend.app.db.session import SessionManager
from backend.app.main import create_app
from backend.app.pipeline.worker import build_redis_settings
from unittest.mock import patch


@pytest.fixture(scope="session", autouse=True)
def _patch_openai_embed_provider():
    async def _fake_embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dimensions for _ in texts]
    with patch("backend.app.llm.embedder.OpenAIEmbedProvider.embed", new=_fake_embed):
        yield

_RUN_INTEGRATION = os.environ.get("COGRAPH_RUN_INTEGRATION") == "1"
_POSTGRES_ADMIN_DSN = os.environ.get(
    "COGRAPH_INTEGRATION_ADMIN_DSN",
    "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
)


def _require_integration_opt_in() -> None:
    if not _RUN_INTEGRATION:
        raise RuntimeError(
            "Set COGRAPH_RUN_INTEGRATION=1 to run Postgres integration tests."
        )


def _to_sqlalchemy_url(raw_dsn: str, database_name: str) -> str:
    base_dsn = raw_dsn.rsplit("/", 1)[0]
    return f"{base_dsn}/{database_name}".replace("postgresql://", "postgresql+asyncpg://", 1)


def _build_alembic_config(database_url: str) -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def integration_database_url() -> str:
    _require_integration_opt_in()

    database_name = f"cograph_it_{uuid.uuid4().hex[:12]}"

    async def _create_database() -> None:
        connection = await asyncpg.connect(_POSTGRES_ADMIN_DSN)
        try:
            await connection.execute(f'CREATE DATABASE "{database_name}"')
        finally:
            await connection.close()

    async def _drop_database() -> None:
        connection = await asyncpg.connect(_POSTGRES_ADMIN_DSN)
        try:
            await connection.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = $1 AND pid <> pg_backend_pid()
                """,
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()

    asyncio.run(_create_database())
    try:
        yield _to_sqlalchemy_url(_POSTGRES_ADMIN_DSN, database_name)
    finally:
        asyncio.run(_drop_database())


@pytest.fixture(scope="session")
def integration_checkout_root() -> Path:
    checkout_root = Path(tempfile.mkdtemp(prefix="cograph-it-checkouts-"))
    try:
        yield checkout_root
    finally:
        shutil.rmtree(checkout_root, ignore_errors=True)


@pytest.fixture(scope="session")
def integration_settings(
    integration_database_url: str,
    integration_checkout_root: Path,
) -> Settings:
    command.upgrade(_build_alembic_config(integration_database_url), "head")
    return Settings(
        environment=Environment.TESTING,
        database=DatabaseSettings(url=integration_database_url, echo=False),
        redis=RedisSettings(url=os.environ.get("COGRAPH_REDIS_URL", "redis://127.0.0.1:6379/15")),
        git=GitSettings(checkouts_root=integration_checkout_root),
        auth=AuthSettings(
            jwt_secret="integration-secret",
            secure_cookies=False,
            registration_enabled=False,
            public_read=True,
        ),
        cors=CorsSettings(allowed_origins=[]),
        embedding=EmbeddingSettings(
            enabled=True,
            api_key="integration-test-embed-key",
        ),
    )


@pytest.fixture
async def integration_session_manager(
    integration_settings: Settings,
) -> AsyncIterator[SessionManager]:
    session_manager = SessionManager(integration_settings)
    redis_pool = await create_pool(build_redis_settings(integration_settings.redis.url))
    try:
        async with session_manager.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE code_node_summaries, code_subgraph_summaries, "
                    "bank_document_chunks, bank_documents, banks, "
                    "documents, repo_document_chunks, repo_documents, code_nodes, "
                    "md_chunks, md_documents, md_collections, md_links, md_jobs, "
                    "repo_sync_runs, repositories, users RESTART IDENTITY CASCADE"
                )
            )
        await redis_pool.flushdb()
        yield session_manager
    finally:
        await redis_pool.aclose()
        await session_manager.dispose()


@pytest.fixture
async def integration_app(integration_settings: Settings):
    app = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def integration_client(integration_app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=integration_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client
