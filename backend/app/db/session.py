from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import Settings


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self._engine = create_async_engine(
            settings.database.url,
            echo=settings.database.echo,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )

        if settings.database.url.startswith("sqlite"):

            @event.listens_for(self._engine.sync_engine, "connect")
            def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        elif settings.database.url.startswith(("postgresql", "postgres")):

            @event.listens_for(self._engine.sync_engine, "connect")
            def _set_statement_timeout(dbapi_connection, _connection_record) -> None:
                # Surface any single statement that hangs >5min as a
                # QueryCanceledError instead of letting it eat the whole
                # 60-minute step deadline. 5min is ~10× the slowest known
                # legitimate batch query (the repo-wide CodeNode SELECT
                # on the largest monorepos).
                cursor = dbapi_connection.cursor()
                cursor.execute("SET statement_timeout = '300s'")
                cursor.close()

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def ping(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self._engine.dispose()
