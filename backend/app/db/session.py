from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import Settings


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        connect_args: dict = {}
        if settings.database.url.startswith(("postgresql", "postgres")):
            # Startup parameters, not `SET`s in a connect hook: with asyncpg
            # those ran inside the connection's first transaction, and when it
            # ended in a rollback (every read-only session does) Postgres undid
            # them. The connection then ran with no statement timeout and the
            # OS keepalive.
            connect_args["server_settings"] = {
                # Surface any single statement that hangs >5min as a
                # QueryCanceledError instead of letting it eat the whole
                # 60-minute step deadline. 5min is ~10x the slowest known
                # legitimate batch query (the repo-wide CodeNode SELECT on
                # the largest monorepos).
                "statement_timeout": "300s",
                # A client whose socket dies silently (node network fault,
                # killed pod) otherwise leaves its session holding row locks
                # for the OS keepalive default of ~2h11m. Probe after 60s idle
                # so the server drops it in ~2min. Not
                # idle_in_transaction_session_timeout: the wiki step keeps a
                # read transaction open for the whole page-writing stage.
                "tcp_keepalives_idle": "60",
                "tcp_keepalives_interval": "10",
                "tcp_keepalives_count": "6",
                # pgvector reads this when the extension loads. Postgres does
                # not reject a bad value here, it falls back to "off", hence
                # the Literal on the setting.
                "hnsw.iterative_scan": settings.retrieval.hnsw_iterative_scan,
            }
        self._engine = create_async_engine(
            settings.database.url,
            echo=settings.database.echo,
            pool_pre_ping=True,
            connect_args=connect_args,
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
