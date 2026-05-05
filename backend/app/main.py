from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from backend.app.api.router import api_router, root_router
from backend.app.api.scim import (
    _SCIMHTTPException,
    SCIM_MEDIA_TYPE,
    router as scim_router,
)
from backend.app.config import Environment, Settings, get_settings
from backend.app.core.bootstrap import generate_bootstrap_token, hash_bootstrap_token
from backend.app.core.errors import register_exception_handlers
from backend.app.core.middleware import install_middleware
from backend.app.core.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from backend.app.db.session import SessionManager
from backend.app.mcp.auth import wrap_with_mcp_auth
from backend.app.mcp.server import build_mcp_services, create_mcp_server
from backend.app.models.enums import UserRole
from backend.app.models.user import User

logger = logging.getLogger(__name__)


async def _install_rate_limiter(app: FastAPI, settings: Settings) -> None:
    if settings.environment in (Environment.DEVELOPMENT, Environment.TESTING):
        logger.info(
            "Rate limiter: using InMemoryRateLimiter (environment=%s). "
            "Redis connection skipped.",
            settings.environment,
        )
        app.state.rate_limiter = InMemoryRateLimiter()
        app.state._rate_limiter_redis = None
        return

    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        redis_client = aioredis.from_url(
            settings.redis.url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await redis_client.ping()
        app.state.rate_limiter = RedisRateLimiter(redis_client)
        app.state._rate_limiter_redis = redis_client
    except Exception as exc:
        if not settings.redis.allow_in_memory_rate_limit_fallback:
            raise RuntimeError(
                "Redis is required for production rate limiting; set "
                "redis.allow_in_memory_rate_limit_fallback=true only for a "
                "single-process emergency fallback."
            ) from exc
        logger.warning(
            "Redis unavailable for rate limiting (%s); falling back to "
            "InMemoryRateLimiter because redis.allow_in_memory_rate_limit_fallback=true. "
            "Rate limits will not be shared across processes.",
            exc,
        )
        app.state.rate_limiter = InMemoryRateLimiter()
        app.state._rate_limiter_redis = None


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    session_manager = SessionManager(resolved_settings)
    mcp_services, _ = build_mcp_services(
        settings=resolved_settings,
        session_manager=session_manager,
    )
    mcp_server = create_mcp_server(
        services=mcp_services,
        streamable_http_path="/",
        # The mounted MCP app sits behind the public web proxy, so the proxy/
        # ingress layer owns external host validation. FastMCP's direct-host
        # DNS-rebinding guard would otherwise reject proxied same-origin hosts.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )
    mcp_http_app = mcp_server.streamable_http_app()

    async def run_mcp_session_manager(*, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        async with mcp_server.session_manager.run():
            task_status.started()
            await anyio.sleep_forever()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.session_manager = session_manager
        app.state.settings = resolved_settings
        app.state.repo_sync_orchestrator = None
        app.state.repo_sync_queue = None
        app.state.summary_generator = None
        app.state.mcp_server = mcp_server

        # Mounted Starlette sub-app lifespans do not start under FastAPI mounts,
        # so the parent app owns the MCP session-manager lifecycle explicitly.
        # Keep the manager in its own task so startup/shutdown happen in the
        # same task, matching the SDK lifecycle contract.
        # In testing: use asyncio.create_task to avoid anyio cancel-scope teardown
        # errors under pytest-asyncio session-scoped fixtures.
        import asyncio

        mcp_task: asyncio.Task | None = None
        if resolved_settings.environment == Environment.TESTING:
            mcp_task = asyncio.create_task(run_mcp_session_manager())
        else:
            async with anyio.create_task_group() as task_group:
                await task_group.start(run_mcp_session_manager)
                await _install_rate_limiter(app, resolved_settings)

                # Embedding role is enforced lazily by callsites
                # (workers, retrieval API, MCP) — first-boot lifespan must
                # come up cleanly so the admin can configure secrets via UI.

                # Bootstrap token: emit a one-time setup token if no admin exists yet.
                # Catch OperationalError in case the schema hasn't been migrated yet —
                # treat that state as "no admin" so the process can still start.
                try:
                    async with session_manager.session() as session:
                        has_admin = await session.scalar(
                            select(User)
                            .where(User.role.in_((UserRole.OWNER, UserRole.ADMIN)))
                            .limit(1)
                        )
                except OperationalError:
                    has_admin = None

                if has_admin is None:
                    token = generate_bootstrap_token()
                    app.state.bootstrap_token_hash = hash_bootstrap_token(token)
                    logger.warning(
                        "\n================================================================\n"
                        "COGRAPH FIRST-RUN SETUP\n"
                        "No owner or admin account exists yet. Open the Cograph web UI in your browser,\n"
                        "go to the /setup page, and enter this one-time setup token:\n"
                        "\n"
                        "  setup_token: %s\n"
                        "\n"
                        "The token expires once an admin is created or the server restarts.\n"
                        "================================================================",
                        token,
                    )
                else:
                    app.state.bootstrap_token_hash = None

                try:
                    yield
                finally:
                    redis_client = getattr(app.state, "_rate_limiter_redis", None)
                    if redis_client is not None:
                        await redis_client.aclose()
                    repo_sync_queue = getattr(app.state, "repo_sync_queue", None)
                    if repo_sync_queue is not None:
                        await repo_sync_queue.aclose()
                    await session_manager.dispose()
                    task_group.cancel_scope.cancel()
            return

        # Testing path continues here (no task_group).
        await _install_rate_limiter(app, resolved_settings)

        # Embedding role is enforced lazily by callsites
        # (workers, retrieval API, MCP) — first-boot lifespan must
        # come up cleanly so the admin can configure secrets via UI.

        # Bootstrap token: emit a one-time setup token if no admin exists yet.
        # Catch OperationalError in case the schema hasn't been migrated yet —
        # treat that state as "no admin" so the process can still start.
        try:
            async with session_manager.session() as session:
                has_admin = await session.scalar(
                    select(User)
                    .where(User.role.in_((UserRole.OWNER, UserRole.ADMIN)))
                    .limit(1)
                )
        except OperationalError:
            has_admin = None

        if has_admin is None:
            token = generate_bootstrap_token()
            app.state.bootstrap_token_hash = hash_bootstrap_token(token)
            logger.warning(
                "\n================================================================\n"
                "COGRAPH FIRST-RUN SETUP\n"
                "No owner or admin account exists yet. Open the Cograph web UI in your browser,\n"
                "go to the /setup page, and enter this one-time setup token:\n"
                "\n"
                "  setup_token: %s\n"
                "\n"
                "The token expires once an admin is created or the server restarts.\n"
                "================================================================",
                token,
            )
        else:
            app.state.bootstrap_token_hash = None

        try:
            yield
        finally:
            redis_client = getattr(app.state, "_rate_limiter_redis", None)
            if redis_client is not None:
                await redis_client.aclose()
            repo_sync_queue = getattr(app.state, "repo_sync_queue", None)
            if repo_sync_queue is not None:
                await repo_sync_queue.aclose()
            await session_manager.dispose()
            if mcp_task is not None:
                mcp_task.cancel()
                try:
                    await mcp_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.version,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.session_manager = session_manager

    install_middleware(app, resolved_settings)
    register_exception_handlers(app)

    @app.exception_handler(_SCIMHTTPException)
    async def _scim_exc_handler(_request, exc: _SCIMHTTPException):  # type: ignore[no-redef]
        import json as _json

        from fastapi.responses import Response as _R

        return _R(
            content=_json.dumps(exc.body),
            status_code=exc.status_code,
            media_type=SCIM_MEDIA_TYPE,
        )

    app.include_router(root_router)
    app.include_router(api_router)
    app.include_router(scim_router)
    app.mount("/mcp", wrap_with_mcp_auth(mcp_http_app, session_manager))
    return app


app = create_app()
