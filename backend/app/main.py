from __future__ import annotations

import logging
import os
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
from backend.app.core.bootstrap import (
    generate_bootstrap_token,
    hash_bootstrap_token,
    resolve_bootstrap_token_path,
)
from backend.app.core.errors import register_exception_handlers
from backend.app.core.logging_config import configure_logging, mask_url
from backend.app.core.middleware import install_middleware
from backend.app.core.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from backend.app.db.session import SessionManager
from backend.app.mcp.auth import wrap_with_mcp_auth
from backend.app.mcp.instructions import refresh_cached_instructions
from backend.app.mcp.server import build_mcp_services, create_mcp_server
from backend.app.models.enums import UserRole
from backend.app.models.user import User

logger = logging.getLogger(__name__)


def _emit_boot_banner(settings: Settings) -> None:
    """Log a single boot banner so operators can see how the process is wired.

    Secrets are masked: passwords inside DB/Redis URLs collapse to ``***``,
    JWT secret never appears, embedding/completion API keys collapse to a
    boolean. The banner is emitted at INFO so production deploys see it
    in `docker logs` on every restart.
    """
    masked_db = mask_url(settings.database.url)
    masked_redis = mask_url(settings.redis.url)
    logger.info(
        "Cograph boot: app=%s version=%s env=%s api_prefix=%s",
        settings.app_name,
        settings.version,
        settings.environment.value,
        settings.api_prefix,
    )
    logger.info(
        "Cograph boot: database.url=%s database.echo=%s",
        masked_db,
        settings.database.echo,
    )
    logger.info(
        "Cograph boot: redis.url=%s redis.allow_in_memory_rate_limit_fallback=%s",
        masked_redis,
        settings.redis.allow_in_memory_rate_limit_fallback,
    )
    logger.info(
        "Cograph boot: auth.registration_enabled=%s auth.public_read=%s "
        "auth.secure_cookies=%s auth.external_url=%s",
        settings.auth.registration_enabled,
        settings.auth.public_read,
        settings.effective_secure_cookies,
        settings.auth.external_url or "(unset)",
    )
    logger.info(
        "Cograph boot: embedding.enabled=%s completion.enabled=%s "
        "completion.preview_enabled=%s",
        settings.embedding.enabled,
        settings.completion.enabled,
        settings.completion.preview_enabled,
    )
    logger.info(
        "Cograph boot: rerank.enabled=%s rerank.provider=%s retrieval.rrf_k=%s "
        "retrieval.candidate_cap=%s",
        settings.retrieval.rerank.enabled,
        settings.retrieval.rerank.provider,
        settings.retrieval.rrf_k,
        settings.retrieval.candidate_cap,
    )
    mcp_protection = bool(settings.mcp.allowed_hosts)
    logger.info(
        "Cograph boot: mcp.dns_rebind_protection=%s mcp.allowed_hosts=%s "
        "mcp.allowed_origins=%s",
        mcp_protection,
        settings.mcp.allowed_hosts or "(unset)",
        settings.mcp.allowed_origins or "(unset)",
    )
    # CRIT-03 visibility: tell operators which derivation mode each
    # cipher is in. `independent` = key derived from a dedicated secret;
    # `jwt-derived` = legacy fallback (still safe, just couples
    # rotation across surfaces).
    llm_mode = "independent" if settings.auth.llm_encryption_secret else "jwt-derived"
    oidc_mode = "independent" if settings.auth.oidc_encryption_secret else "jwt-derived"
    logger.info(
        "Cograph boot: llm_secret_cipher=%s oidc_secret_cipher=%s",
        llm_mode,
        oidc_mode,
    )
    # In production, nudge operators toward the independent-secrets path.
    # `jwt-derived` is still safe but means a JWT-secret leak compromises
    # at-rest provider/IdP credentials too. The fix is a settings change
    # plus `cograph-backend reencrypt-secrets` (CRIT-03 phase 2).
    if settings.environment is Environment.PRODUCTION and (
        settings.auth.llm_encryption_secret is None
        or settings.auth.oidc_encryption_secret is None
    ):
        logger.warning(
            "Cograph boot: at-rest secret encryption falls back to jwt_secret "
            "(llm=%s, oidc=%s). Set auth.llm_encryption_secret + "
            "auth.oidc_encryption_secret and run "
            "`cograph-backend reencrypt-secrets` to decouple rotation.",
            llm_mode,
            oidc_mode,
        )


async def _maybe_emit_bootstrap_token(
    app: FastAPI, session_manager: SessionManager
) -> None:
    """Provision the one-time bootstrap token when no admin exists yet.

    Writes the plaintext token to ``$COGRAPH_BOOTSTRAP_TOKEN_FILE`` (default
    ``./.cograph/bootstrap.token``) with 0600 permissions and logs only the
    file path + a masked prefix. The token never enters the structured log
    stream where centralised collectors can index it. The hash is held in
    ``app.state.bootstrap_token_hash`` for the consumer endpoint to verify
    against; once consumed (or once an admin appears) the file is removed.

    Catches ``OperationalError`` in case the schema hasn't been migrated yet
    — that state is treated as "no admin" so the process can still start.
    """
    try:
        async with session_manager.session() as session:
            has_admin = await session.scalar(
                select(User)
                .where(User.role.in_((UserRole.OWNER, UserRole.ADMIN)))
                .limit(1)
            )
    except OperationalError:
        has_admin = None

    if has_admin is not None:
        app.state.bootstrap_token_hash = None
        return

    token = generate_bootstrap_token()
    app.state.bootstrap_token_hash = hash_bootstrap_token(token)

    token_path = resolve_bootstrap_token_path()
    masked = token[:4] + "*" * 8
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically with restrictive perms so a concurrent reader
        # never sees a half-written file and only the process owner can
        # read the resulting file.
        fd = os.open(
            str(token_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "w") as fh:
            fh.write(token + "\n")
        location_hint = str(token_path)
    except OSError as exc:
        # Fallback: keep the token in memory only and tell the operator
        # to restart with a writable token-file location. This keeps
        # boot working in a read-only container without leaking the token.
        logger.error(
            "Could not write bootstrap token file at %s: %s. "
            "The setup endpoint will still accept the token if you can "
            "read it from this banner; otherwise restart with "
            "COGRAPH_BOOTSTRAP_TOKEN_FILE pointing to a writable path.",
            token_path,
            exc,
        )
        location_hint = "(in-memory only — see error log)"

    logger.warning(
        "\n================================================================\n"
        "COGRAPH FIRST-RUN SETUP\n"
        "No owner or admin account exists yet. Open the Cograph web UI in your browser,\n"
        "go to the /setup page, and paste the one-time setup token from:\n"
        "\n"
        "  token_file: %s\n"
        "  token_prefix: %s   (sanity-check the file contents start with this)\n"
        "\n"
        "The token expires once an admin is created or the server restarts.\n"
        "================================================================",
        location_hint,
        masked,
    )


async def _probe_oidc_providers(
    session_manager: SessionManager, settings: Settings
) -> None:
    """Best-effort discovery probe per enabled OIDC provider.

    Logs INFO on success (issuer + endpoints reached) and WARNING on
    failure (network / TLS / discovery JSON malformed) so a misconfigured
    IdP shows up in logs on the very first restart instead of waiting
    for the first user to click 'Sign in'.
    """
    from backend.app.auth.oidc_cipher import OIDCSecretCipher
    from backend.app.auth.oidc_client import OIDCClient
    from backend.app.models.identity_provider import IdentityProvider

    try:
        async with session_manager.session() as session:
            providers = (
                await session.scalars(
                    select(IdentityProvider).where(IdentityProvider.enabled.is_(True))
                )
            ).all()
    except OperationalError:
        # Schema not migrated yet — skip silently (boot must still come up).
        return

    if not providers:
        logger.info("OIDC: no enabled identity providers configured")
        return

    cipher = OIDCSecretCipher(settings)
    for provider in providers:
        secret = (
            cipher.decrypt(provider.client_secret_encrypted)
            if provider.client_secret_encrypted
            else None
        )
        client = OIDCClient(
            issuer_url=provider.issuer_url,
            client_id=provider.client_id,
            client_secret=secret,
            scopes=list(provider.scopes),
        )
        try:
            doc = await client.discovery()
            logger.info(
                "OIDC[%s]: discovery ok issuer=%s authorize=%s token=%s jwks=%s",
                provider.slug,
                doc.issuer,
                doc.authorization_endpoint,
                doc.token_endpoint,
                doc.jwks_uri,
            )
        except Exception as exc:  # ApiError or transport error
            logger.warning(
                "OIDC[%s]: discovery failed issuer=%s err=%s",
                provider.slug,
                provider.issuer_url,
                exc,
            )
        finally:
            await client.aclose()


async def _refresh_mcp_instructions(
    session_manager: SessionManager, settings: Settings
) -> None:
    """Load the operator briefing from DB and seed the MCP instructions cache.

    Best-effort — if the table isn't present yet (migration window) we log
    and move on; the cache already holds the bootstrap default that
    `create_mcp_server` seeded, so MCP clients keep getting a coherent
    `instructions=` until the next reboot or admin PATCH.
    """
    try:
        async with session_manager.session() as session:
            await refresh_cached_instructions(session, settings=settings)
            logger.info("MCP instructions: refreshed from DB on boot")
    except OperationalError:
        logger.warning(
            "MCP instructions: skipping DB refresh — table not migrated yet"
        )


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
    configure_logging(resolved_settings)
    session_manager = SessionManager(resolved_settings)
    mcp_services, _ = build_mcp_services(
        settings=resolved_settings,
        session_manager=session_manager,
    )
    # DNS-rebinding protection: FastMCP validates Host/Origin against
    # configured allowlists when enabled. We auto-enable when the
    # operator has populated `mcp.allowed_hosts` (= they took an
    # explicit deployment decision); empty list keeps protection off so
    # a fresh `docker compose up` doesn't 421 every request. Operators
    # who want the protection set:
    #   COGRAPH_MCP__ALLOWED_HOSTS='["cograph.example.com"]'
    #   COGRAPH_MCP__ALLOWED_ORIGINS='["https://cograph.example.com"]'
    mcp_protection_enabled = bool(resolved_settings.mcp.allowed_hosts)
    mcp_server = create_mcp_server(
        services=mcp_services,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=mcp_protection_enabled,
            allowed_hosts=list(resolved_settings.mcp.allowed_hosts),
            allowed_origins=list(resolved_settings.mcp.allowed_origins),
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

        _emit_boot_banner(resolved_settings)

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
                await _probe_oidc_providers(session_manager, resolved_settings)

                # Embedding role is enforced lazily by callsites
                # (workers, retrieval API, MCP) — first-boot lifespan must
                # come up cleanly so the admin can configure secrets via UI.
                await _maybe_emit_bootstrap_token(app, session_manager)
                await _refresh_mcp_instructions(session_manager, resolved_settings)

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
        await _probe_oidc_providers(session_manager, resolved_settings)

        # Embedding role is enforced lazily by callsites
        # (workers, retrieval API, MCP) — first-boot lifespan must
        # come up cleanly so the admin can configure secrets via UI.
        await _maybe_emit_bootstrap_token(app, session_manager)
        await _refresh_mcp_instructions(session_manager, resolved_settings)

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

    # Hide the interactive OpenAPI surface outside development. The schema
    # is a complete map of every endpoint — convenient for developers,
    # unnecessary surface area on a self-hosted production deployment.
    # Tests run under Environment.TESTING and don't need /docs either, so
    # only the development environment exposes them.
    docs_enabled = resolved_settings.is_development
    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.version,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
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
