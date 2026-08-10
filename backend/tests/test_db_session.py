from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

from backend.app.config import (
    AuthSettings,
    CorsSettings,
    DatabaseSettings,
    EmbeddingSettings,
    Environment,
    GitSettings,
    RedisSettings,
    Settings,
)
from backend.app.db.session import SessionManager


def _build_settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(
        environment=Environment.TESTING,
        database=DatabaseSettings(url=database_url, echo=False),
        redis=RedisSettings(url="redis://localhost:6379/15"),
        git=GitSettings(checkouts_root=tmp_path / "checkouts"),
        auth=AuthSettings(
            jwt_secret="test-secret",
            secure_cookies=False,
            registration_enabled=False,
            public_read=True,
        ),
        cors=CorsSettings(allowed_origins=[]),
        embedding=EmbeddingSettings(enabled=True, api_key="test-embed-key"),
    )


def _our_connect_listeners(manager: SessionManager) -> list:
    """Return only the `connect` listeners SessionManager defined in
    `backend/app/db/session.py` — i.e. skip dialect / pool framework
    listeners that fire on every engine.
    """
    listeners = list(manager.engine.sync_engine.pool.dispatch.connect)
    ours: list = []
    for listener in listeners:
        try:
            source_file = inspect.getsourcefile(listener) or ""
        except TypeError:
            continue
        if source_file.endswith("backend/app/db/session.py"):
            ours.append(listener)
    return ours


async def test_postgres_session_sets_statement_timeout_on_connect(tmp_path):
    """For Postgres URLs the session manager must run
    `SET statement_timeout = '300s'` on every new connection so a single
    hanging query surfaces in 5 minutes instead of letting the 60-minute
    step deadline eat the whole pipeline.
    """
    settings = _build_settings(
        "postgresql+asyncpg://user:pass@localhost:5432/notreal", tmp_path
    )
    manager = SessionManager(settings)
    try:
        cursor = MagicMock()
        dbapi_connection = MagicMock()
        dbapi_connection.cursor.return_value = cursor

        listeners = _our_connect_listeners(manager)
        assert listeners, "expected SessionManager to register a `connect` listener"
        for listener in listeners:
            listener(dbapi_connection, None)

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        assert "SET statement_timeout = '300s'" in executed
    finally:
        await manager.dispose()


async def test_sqlite_session_does_not_issue_statement_timeout(tmp_path):
    """SQLite (the test DB) must skip the Postgres-only timeout SET — it
    would fail with a syntax error on SQLite.
    """
    settings = _build_settings(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}", tmp_path)
    manager = SessionManager(settings)
    try:
        cursor = MagicMock()
        dbapi_connection = MagicMock()
        dbapi_connection.cursor.return_value = cursor

        listeners = _our_connect_listeners(manager)
        for listener in listeners:
            listener(dbapi_connection, None)

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        # SQLite path enables foreign keys but must never issue the
        # Postgres-specific timeout statement.
        assert all("statement_timeout" not in stmt for stmt in executed)
        assert "PRAGMA foreign_keys=ON" in executed
    finally:
        await manager.dispose()
