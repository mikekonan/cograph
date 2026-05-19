from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from backend.app.llm.summary_generator import SummaryGenerator

from arq import create_pool
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.auth.actor import ALL_SCOPES, AuthenticatedActor
from backend.app.config import Settings
from backend.app.core.auth import TokenType, decode_token, extract_access_token
from backend.app.core.errors import ApiError
from backend.app.core.rate_limit import RateLimiter
from backend.app.models.enums import UserRole
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.user import User
from backend.app.pipeline.checkout import GitCheckoutAdapter
from backend.app.pipeline.orchestrator import RepoSyncOrchestrator, RepoSyncQueue
from backend.app.pipeline.worker import build_redis_settings
from backend.app.pipeline.zip_checkout import ZipCheckoutAdapter

logger = logging.getLogger(__name__)

PAT_PLAINTEXT_PREFIX = "cgr_pat_"
PAT_PREFIX_DISPLAY_CHARS = 16
_PAT_LAST_USED_REFRESH_SECONDS = 60


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_manager = request.app.state.session_manager
    async with session_manager.session() as session:
        yield session


async def require_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    bearer = _extract_bearer(request)
    if bearer is not None and bearer.startswith(PAT_PLAINTEXT_PREFIX):
        actor = await _resolve_pat_or_raise(
            bearer,
            session,
            client_ip=_request_client_ip(request),
            required_scope=_required_pat_scope_for_method(request.method),
        )
        request.state.user_id = str(actor.user.id)
        request.state.auth_method = "pat"
        return actor.user

    token = extract_access_token(request, settings)
    if token is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")

    claims = decode_token(
        token,
        settings=settings,
        expected_type=TokenType.ACCESS,
    )
    user = await session.get(User, claims.user_id)
    if user is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    if not user.is_active:
        raise ApiError(401, "UNAUTHENTICATED", "Account is disabled")

    request.state.user_id = str(user.id)
    request.state.auth_method = "cookie_jwt"
    return user


async def get_current_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> User | None:
    bearer = _extract_bearer(request)
    if bearer is not None and bearer.startswith(PAT_PLAINTEXT_PREFIX):
        actor = await _resolve_pat_or_raise(
            bearer,
            session,
            client_ip=_request_client_ip(request),
            required_scope="api:read",
        )
        return actor.user

    token = extract_access_token(request, settings)
    if token is None:
        return None

    claims = decode_token(
        token,
        settings=settings,
        expected_type=TokenType.ACCESS,
    )
    user = await session.get(User, claims.user_id)
    if user is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    if not user.is_active:
        raise ApiError(401, "UNAUTHENTICATED", "Account is disabled")
    return user


async def require_admin_or_owner(
    current_user: User = Depends(require_current_user),
) -> User:
    """Admin/owner gate. OWNER is a label on the bootstrap user; the
    role grants no privilege over ADMIN — both pass this gate."""
    if current_user.role not in (UserRole.OWNER, UserRole.ADMIN):
        raise ApiError(403, "FORBIDDEN", "Administrator access required")
    return current_user


require_admin = require_admin_or_owner


def _hash_pat(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    value = header.strip()
    if not value.lower().startswith("bearer "):
        return None
    return value[7:].strip() or None


def _request_client_ip(request: Request) -> str | None:
    return (request.client.host if request.client else None) or None


def _required_pat_scope_for_method(method: str) -> str:
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return "api:read"
    return "api:write"


async def _resolve_pat_or_raise(
    bearer: str,
    session: AsyncSession,
    *,
    client_ip: str | None,
    required_scope: str,
) -> AuthenticatedActor:
    actor = await _resolve_pat(bearer, session, client_ip=client_ip)
    if actor is None:
        logger.warning(
            "PAT auth rejected: ip=%s prefix=%s reason=unknown_or_revoked_or_expired",
            client_ip,
            bearer[:PAT_PREFIX_DISPLAY_CHARS],
        )
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    if required_scope not in actor.scopes:
        logger.warning(
            "PAT scope rejected: user_id=%s token_id=%s required=%s scopes=%s",
            actor.user.id,
            actor.token_id,
            required_scope,
            sorted(actor.scopes),
        )
        raise ApiError(
            403,
            "INSUFFICIENT_SCOPE",
            f"Token is missing required scope: {required_scope}",
        )
    return actor


async def _resolve_pat(
    bearer: str, session: AsyncSession, *, client_ip: str | None
) -> AuthenticatedActor | None:
    """Look up a personal access token and return its actor.

    Returns None for any rejection (unknown / revoked / expired / disabled
    user). The caller decides whether to fall through to other auth
    methods (REST) or 401 immediately (MCP).
    """
    if not bearer.startswith(PAT_PLAINTEXT_PREFIX):
        return None
    h = _hash_pat(bearer)
    row = await session.scalar(
        select(PersonalAccessToken)
        .options(selectinload(PersonalAccessToken.user))
        .where(PersonalAccessToken.token_hash == h)
    )
    if row is None or row.revoked_at is not None:
        return None
    now = datetime.now(UTC)
    if row.expires_at is not None:
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now:
            return None
    if not row.user.is_active:
        return None

    last_used = row.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=UTC)
    needs_refresh = (
        last_used is None
        or (now - last_used).total_seconds() >= _PAT_LAST_USED_REFRESH_SECONDS
    )
    if needs_refresh:
        row.last_used_at = now
        if client_ip:
            row.last_used_ip = client_ip[:64]
        try:
            await session.commit()
        except Exception:  # ledger must not fail the request
            await session.rollback()

    return AuthenticatedActor(
        user=row.user,
        method="pat",
        scopes=frozenset(row.scopes),
        token_id=row.id,
    )


async def require_authenticated(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> AuthenticatedActor:
    """Phase 30.2 unified resolver — PAT (Bearer cgr_pat_…) → cookie session.

    A bearer-JWT method exists in the dataclass for future use but the
    REST surface still relies on the cookie session for browser flows;
    this entry point consolidates PAT and cookie auth.
    """
    client_ip = _request_client_ip(request)

    bearer = _extract_bearer(request)
    if bearer is not None and bearer.startswith(PAT_PLAINTEXT_PREFIX):
        actor = await _resolve_pat(bearer, session, client_ip=client_ip)
        if actor is None:
            logger.warning(
                "PAT auth rejected: ip=%s prefix=%s reason=unknown_or_revoked_or_expired",
                client_ip,
                bearer[:PAT_PREFIX_DISPLAY_CHARS],
            )
            raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
        request.state.user_id = str(actor.user.id)
        request.state.auth_method = "pat"
        return actor

    user = await require_current_user(request=request, session=session, settings=settings)
    return AuthenticatedActor(
        user=user,
        method="cookie_jwt",
        scopes=ALL_SCOPES,
        token_id=None,
    )


def require_scope(scope: str):
    """Reject PAT actors missing the named scope; cookie/jwt auto-pass."""

    async def gate(
        actor: AuthenticatedActor = Depends(require_authenticated),
    ) -> AuthenticatedActor:
        if actor.method != "pat":
            return actor
        if scope not in actor.scopes:
            logger.warning(
                "PAT scope rejected: user_id=%s token_id=%s required=%s scopes=%s",
                actor.user.id,
                actor.token_id,
                scope,
                sorted(actor.scopes),
            )
            raise ApiError(
                403,
                "INSUFFICIENT_SCOPE",
                f"Token is missing required scope: {scope}",
            )
        return actor

    return gate


async def require_actor_csrf(
    request: Request,
    actor: AuthenticatedActor = Depends(require_authenticated),
    settings: Settings = Depends(get_settings_dep),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthenticatedActor:
    """CSRF gate that no-ops for PAT actors.

    Browser sessions need double-submit CSRF; PAT clients are not subject
    to ambient cookie auth so CSRF is moot. Endpoints that already rely
    on `require_authenticated` should switch to this dep instead of
    layering `require_csrf` (which forces a cookie session).
    """
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return actor
    if actor.method == "pat":
        if "api:write" not in actor.scopes:
            logger.warning(
                "PAT scope rejected: user_id=%s token_id=%s required=api:write scopes=%s",
                actor.user.id,
                actor.token_id,
                sorted(actor.scopes),
            )
            raise ApiError(
                403,
                "INSUFFICIENT_SCOPE",
                "Token is missing required scope: api:write",
            )
        return actor

    if not x_csrf_token:
        logger.warning(
            "CSRF rejected: user_id=%s reason=missing_header path=%s",
            actor.user.id,
            request.url.path,
        )
        raise ApiError(403, "CSRF_INVALID", "X-CSRF-Token header is required")

    token = extract_access_token(request, settings)
    if token is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    claims = decode_token(token, settings=settings, expected_type=TokenType.ACCESS)

    expected_csrf = claims.csrf or ""
    if not secrets.compare_digest(x_csrf_token, expected_csrf):
        logger.warning(
            "CSRF rejected: user_id=%s reason=token_mismatch path=%s",
            actor.user.id,
            request.url.path,
        )
        raise ApiError(403, "CSRF_INVALID", "CSRF token mismatch")
    return actor


async def require_csrf(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    settings: Settings = Depends(get_settings_dep),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> User:
    """
    Validates the CSRF double-submit cookie pattern for mutating requests.

    Every POST/PUT/PATCH/DELETE must supply the
    value of the `cograph_csrf` cookie in the `X-CSRF-Token` header.  The server
    compares it timing-safely with the `csrf` claim embedded in the access JWT.

    Depends on `require_current_user` so authentication failures (missing /
    expired / invalid access token) surface as 401 *before* this dep runs — the
    silent-refresh flow in the web client relies on that ordering.

    Exemptions (must NOT wire this dep on):
    - /auth/login, /auth/refresh  — no session exists yet
    - /auth/logout                — clearing cookies unauthenticated is fine
    """
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return current_user

    bearer = _extract_bearer(request)
    if bearer is not None and bearer.startswith(PAT_PLAINTEXT_PREFIX):
        actor = await _resolve_pat_or_raise(
            bearer,
            session,
            client_ip=_request_client_ip(request),
            required_scope="api:write",
        )
        return actor.user

    if not x_csrf_token:
        raise ApiError(403, "CSRF_INVALID", "X-CSRF-Token header is required")

    # require_current_user already validated the token; re-decoding just to
    # read the csrf claim is cheap (HS256 verify) and avoids changing its API.
    # NOTE: bare assert would be stripped under `python -O`; raise explicitly.
    token = extract_access_token(request, settings)
    if token is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    claims = decode_token(token, settings=settings, expected_type=TokenType.ACCESS)

    expected_csrf = claims.csrf or ""
    if not secrets.compare_digest(x_csrf_token, expected_csrf):
        raise ApiError(403, "CSRF_INVALID", "CSRF token mismatch")

    return current_user


def get_summary_generator(request: Request) -> "SummaryGenerator | None":
    from backend.app.llm.summary_generator import SummaryGenerator

    cached = getattr(request.app.state, "summary_generator", None)
    if isinstance(cached, SummaryGenerator):
        return cached

    settings: Settings = request.app.state.settings
    if not settings.completion.enabled:
        return None

    from backend.app.llm.completion import OpenAICompletionProvider

    provider = OpenAICompletionProvider(
        api_url=settings.completion.api_url,
        api_key=settings.completion.api_key.get_secret_value(),
        model=settings.completion.model,
        request_timeout_seconds=settings.completion.request_timeout_seconds,
        connect_timeout_seconds=settings.completion.connect_timeout_seconds,
    )
    generator = SummaryGenerator(llm=provider)
    request.app.state.summary_generator = generator
    return generator


async def get_repo_sync_orchestrator(request: Request) -> RepoSyncOrchestrator:
    orchestrator = getattr(request.app.state, "repo_sync_orchestrator", None)
    if isinstance(orchestrator, RepoSyncOrchestrator):
        return orchestrator

    queue = getattr(request.app.state, "repo_sync_queue", None)
    if queue is None:
        settings: Settings = request.app.state.settings
        try:
            queue = await create_pool(build_redis_settings(settings.redis.url))
        except Exception as exc:
            raise ApiError(
                503,
                "SERVICE_UNAVAILABLE",
                "Worker queue unavailable",
            ) from exc
        request.app.state.repo_sync_queue = queue

    settings = request.app.state.settings
    orchestrator = RepoSyncOrchestrator(
        job_queue=cast(RepoSyncQueue, queue),
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
        zip_checkout_adapter=build_zip_checkout_adapter(settings),
        settings=settings,
    )
    request.app.state.repo_sync_orchestrator = orchestrator
    return orchestrator


def build_zip_checkout_adapter(settings: Settings) -> ZipCheckoutAdapter:
    return ZipCheckoutAdapter(
        checkouts_root=settings.git.checkouts_root,
        max_compressed_bytes=settings.archive_upload.max_compressed_bytes,
        max_decompressed_bytes=settings.archive_upload.max_decompressed_bytes,
        max_per_file_bytes=settings.archive_upload.max_per_file_bytes,
        max_inflation_ratio=settings.archive_upload.max_inflation_ratio,
        max_entries=settings.archive_upload.max_entries,
    )


def get_zip_checkout_adapter(request: Request) -> ZipCheckoutAdapter:
    """Per-request `ZipCheckoutAdapter` shared with the orchestrator.

    Cached on `app.state` so repeated requests reuse one instance and we
    don't re-bind the checkouts_root path or recompute caps on every
    upload.
    """
    state = request.app.state
    adapter: ZipCheckoutAdapter | None = getattr(state, "zip_checkout_adapter", None)
    if adapter is None:
        settings: Settings = state.settings
        adapter = build_zip_checkout_adapter(settings)
        state.zip_checkout_adapter = adapter
    return adapter
