from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from backend.app.config import (
    AuthSettings,
    CorsSettings,
    DatabaseSettings,
    EmbeddingSettings,
    Environment,
    GitSettings,
    RedisSettings,
    RetrievalSettings,
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


async def test_sqlite_session_enables_foreign_keys(tmp_path):
    """SQLite (the test DB) turns foreign keys on for every connection."""
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
        assert executed == ["PRAGMA foreign_keys=ON"]
    finally:
        await manager.dispose()


def test_hnsw_iterative_scan_rejects_a_typo() -> None:
    # Postgres would silently treat an unknown value as "off".
    with pytest.raises(ValidationError):
        RetrievalSettings(hnsw_iterative_scan="relaxed")
