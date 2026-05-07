from __future__ import annotations
import logging
import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, computed_field
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError

from backend.app.config import Settings
from backend.app.core.auth import (
    TokenType,
    create_token,
    decode_token,
    hash_password,
    validate_password_length,
    verify_password,
)
from backend.app.core.bootstrap import hash_bootstrap_token
from backend.app.core.deps import get_db_session, get_rate_limiter, get_settings_dep, require_current_user
from backend.app.core.errors import ApiError, build_error_response
from backend.app.core.rate_limit import RateLimiter
from backend.app.models.enums import UserRole
from backend.app.models.identity_provider import IdentityProvider
from backend.app.models.refresh_token_family import RefreshTokenFamily
from backend.app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Rate-limit windows / caps for login attempts.
_WINDOW_SECONDS = 900  # 15 minutes
_IP_LIMIT = 20         # attempts per IP per window (any outcome)
_EMAIL_LIMIT = 5       # failed attempts per email per window


class AuthProviderConfig(BaseModel):
    """Provider entry shown on the login page.

    `kind="password"` is implicit + always present when registration /
    bootstrap allows it. `kind="oidc"` rows are sourced from
    `identity_providers` rows where `enabled=true`.
    """

    kind: str
    slug: str | None = None
    display_name: str | None = None
    login_url: str | None = None
    enabled: bool = True


class AuthConfigResponse(BaseModel):
    registration_enabled: bool
    public_read: bool
    providers: list[AuthProviderConfig]
    needs_bootstrap: bool


class UserResponse(BaseModel):
    id: UUID
    email: str
    name: str | None
    role: UserRole
    is_active: bool
    deactivated_reason: str | None = None
    auth_source: str
    last_login_at: datetime | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_owner(self) -> bool:
        # Back-compat for FE that still reads `is_owner`. The wire field
        # is computed from the role enum (Phase 30.1 dropped the column).
        return self.role is UserRole.OWNER


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user: UserResponse


def _to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        deactivated_reason=user.deactivated_reason,
        auth_source=user.auth_source,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    """Set the access, refresh, and CSRF auth cookies."""
    secure = settings.effective_secure_cookies
    response.set_cookie(
        key=settings.auth.access_cookie_name,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api",
        max_age=settings.auth.access_token_ttl_seconds,
    )
    response.set_cookie(
        key=settings.auth.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/auth",
        max_age=settings.auth.refresh_token_ttl_seconds,
    )
    # cograph_csrf is NOT httponly -- JS needs to read it for the double-submit pattern.
    response.set_cookie(
        key=settings.auth.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=settings.auth.access_token_ttl_seconds,
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Clear all three auth cookies by setting Max-Age=0."""
    secure = settings.effective_secure_cookies
    response.delete_cookie(
        key=settings.auth.access_cookie_name,
        path="/api",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.auth.refresh_cookie_name,
        path="/api/auth",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.auth.csrf_cookie_name,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


def _rate_limited_response(request: Request, retry_after: int) -> JSONResponse:
    """Build a 429 JSON response including error.request_id per FE_CONTRACT.

    Routes through build_error_response so the envelope is consistent with all
    other error paths (error.request_id, X-Request-ID header, etc.).
    """
    return build_error_response(
        request,
        status_code=429,
        code="RATE_LIMITED",
        message="Too many login attempts. Try again later.",
        headers={"Retry-After": str(retry_after)},
    )


@router.get("/config", response_model=AuthConfigResponse)
async def get_auth_config(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> AuthConfigResponse:
    try:
        has_admin = (
            await session.scalar(
                select(User.id)
                .where(User.role.in_((UserRole.OWNER, UserRole.ADMIN)))
                .limit(1)
            )
            is not None
        )
    except OperationalError:
        # In early startup (before migrations) or during transient DB issues,
        # fall back to the in-memory bootstrap token state to keep the endpoint
        # non-fatal for the web UI.
        has_admin = None

    if has_admin is True:
        # If an admin appears (e.g. via CLI) we must immediately exit bootstrap
        # mode without requiring a backend restart.
        request.app.state.bootstrap_token_hash = None
        needs_bootstrap = False
    elif has_admin is False:
        needs_bootstrap = True
    else:
        needs_bootstrap = getattr(request.app.state, "bootstrap_token_hash", None) is not None
    providers: list[AuthProviderConfig] = [AuthProviderConfig(kind="password")]
    try:
        oidc_rows = (
            await session.scalars(
                select(IdentityProvider)
                .where(IdentityProvider.enabled.is_(True))
                .order_by(IdentityProvider.created_at.asc())
            )
        ).all()
    except OperationalError:
        oidc_rows = []
    for provider in oidc_rows:
        providers.append(
            AuthProviderConfig(
                kind="oidc",
                slug=provider.slug,
                display_name=provider.display_name,
                login_url=f"/api/auth/oidc/{provider.slug}/login",
                enabled=True,
            )
        )

    return AuthConfigResponse(
        registration_enabled=settings.auth.registration_enabled,
        public_read=settings.auth.public_read,
        providers=providers,
        needs_bootstrap=needs_bootstrap,
    )


class BootstrapRequest(BaseModel):
    setup_token: str
    email: EmailStr
    password: str
    name: str = "Admin"


@router.post("/bootstrap", response_model=LoginResponse)
async def bootstrap_admin(
    request: Request,
    payload: BootstrapRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> LoginResponse | Response:
    """Create the first admin when no admin exists yet.

    The one-time setup_token is printed to stdout at startup and held only in
    memory — it is never persisted to the database.
    """
    stored_hash: str | None = getattr(request.app.state, "bootstrap_token_hash", None)

    if stored_hash is None:
        raise ApiError(409, "ADMIN_ALREADY_EXISTS", "An admin user already exists.")

    # Rate-limit bootstrap attempts by IP to prevent brute-forcing the token.
    client_ip = (request.client.host if request.client else None) or "unknown"
    ip_result = await rate_limiter.hit(
        f"rate:bootstrap:ip:{client_ip}",
        window_seconds=_WINDOW_SECONDS,
        limit=_IP_LIMIT,
    )
    if not ip_result.allowed:
        return _rate_limited_response(request, ip_result.retry_after_seconds)

    # Constant-time token comparison via SHA-256 hash.
    supplied_hash = hash_bootstrap_token(payload.setup_token)
    if not secrets.compare_digest(supplied_hash, stored_hash):
        logger.warning("Bootstrap attempt with invalid setup_token from %s", client_ip)
        raise ApiError(401, "BOOTSTRAP_TOKEN_INVALID", "Invalid setup token.")

    # Validate password length before doing any DB work.
    try:
        validate_password_length(payload.password)
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_FAILED", str(exc)) from exc

    # Double-check no privileged user (owner or admin) snuck in concurrently.
    existing_admin = await session.scalar(
        select(User)
        .where(User.role.in_((UserRole.OWNER, UserRole.ADMIN)))
        .limit(1)
    )
    if existing_admin is not None:
        request.app.state.bootstrap_token_hash = None
        raise ApiError(409, "ADMIN_ALREADY_EXISTS", "An admin user already exists.")

    # Create the bootstrap admin as the singleton OWNER (Phase 30.1).
    # Subsequent users are USER by default and only the owner can promote.
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name,
        role=UserRole.OWNER,
        auth_source="password",
    )
    session.add(user)
    # Flush to populate user.id (needed for RefreshTokenFamily FK below).
    await session.flush()

    # Issue tokens so the client is immediately logged in.
    csrf_token = secrets.token_hex(32)
    family = uuid4()
    refresh_jti = uuid4()

    access_token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=csrf_token,
    )
    refresh_token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.REFRESH,
        family=family,
        jti=refresh_jti,
    )

    family_row = RefreshTokenFamily(
        family=family,
        user_id=user.id,
        current_jti=refresh_jti,
    )
    session.add(family_row)
    await session.commit()

    # Consume the bootstrap token — no further bootstrap calls are accepted.
    request.app.state.bootstrap_token_hash = None
    logger.info("Bootstrap admin created: %s", user.email)

    _set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        settings=settings,
    )

    return LoginResponse(user=_to_user_response(user))


@router.post("/register")
async def register_disabled() -> None:
    raise ApiError(
        403,
        "FORBIDDEN",
        "Self-registration is disabled. Contact your administrator.",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> LoginResponse | Response:
    # Order: check IP (hit) -> check email (read-only) -> look up user
    # -> verify password -> if bad, bump email counter (hit).
    client_ip = (request.client.host if request.client else None) or "unknown"

    # 1. Per-IP counter.
    ip_result = await rate_limiter.hit(
        f"rate:login:ip:{client_ip}",
        window_seconds=_WINDOW_SECONDS,
        limit=_IP_LIMIT,
    )
    if not ip_result.allowed:
        return _rate_limited_response(request, ip_result.retry_after_seconds)

    # 2. Per-email pre-check (read-only).
    email_check = await rate_limiter.check(
        f"rate:login:email:{payload.email}",
        window_seconds=_WINDOW_SECONDS,
        limit=_EMAIL_LIMIT,
    )
    if not email_check.allowed:
        return _rate_limited_response(request, email_check.retry_after_seconds)

    # DB lookup + password verification.
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    invalid = (
        user is None
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    )
    if invalid:
        email_fail = await rate_limiter.hit(
            f"rate:login:email:{payload.email}",
            window_seconds=_WINDOW_SECONDS,
            limit=_EMAIL_LIMIT,
        )
        if not email_fail.allowed:
            logger.warning(
                "Login rate-limited: email=%s ip=%s",
                payload.email,
                client_ip,
            )
            return _rate_limited_response(request, email_fail.retry_after_seconds)
        logger.warning(
            "Login failed: email=%s ip=%s reason=invalid_credentials",
            payload.email,
            client_ip,
        )
        raise ApiError(401, "UNAUTHENTICATED", "Invalid credentials")

    # Disabled accounts cannot log in even with the right password.
    assert user is not None  # narrow for type-checkers; falsy paths returned above
    if not user.is_active:
        logger.warning(
            "Login failed: email=%s ip=%s reason=account_disabled",
            payload.email,
            client_ip,
        )
        raise ApiError(401, "UNAUTHENTICATED", "Account is disabled")

    # Successful login -- issue tokens.
    csrf_token = secrets.token_hex(32)
    family = uuid4()
    refresh_jti = uuid4()

    access_token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=csrf_token,
    )
    refresh_token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.REFRESH,
        family=family,
        jti=refresh_jti,
    )

    family_row = RefreshTokenFamily(
        family=family,
        user_id=user.id,
        current_jti=refresh_jti,
    )
    session.add(family_row)
    user.last_login_at = datetime.now(UTC)
    await session.commit()

    _set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        settings=settings,
    )

    logger.info(
        "Login ok: user_id=%s email=%s role=%s ip=%s",
        user.id,
        user.email,
        user.role.value,
        client_ip,
    )

    return LoginResponse(user=_to_user_response(user))


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    refresh_cookie = request.cookies.get(settings.auth.refresh_cookie_name)
    user_id_for_log: UUID | None = None
    if refresh_cookie:
        try:
            claims = decode_token(
                refresh_cookie,
                settings=settings,
                expected_type=TokenType.REFRESH,
            )
            user_id_for_log = claims.user_id
            if claims.family:
                family_row = await session.get(RefreshTokenFamily, claims.family)
                if family_row is not None and family_row.revoked_at is None:
                    family_row.revoked_at = datetime.now(UTC)
                    await session.commit()
        except ApiError:
            pass

    _clear_auth_cookies(response, settings)
    logger.info("Logout: user_id=%s", user_id_for_log)


@router.post("/refresh", response_model=LoginResponse)
async def refresh_tokens(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> LoginResponse:
    """Rotate refresh token, check family reuse, issue new pair."""
    refresh_cookie = request.cookies.get(settings.auth.refresh_cookie_name)
    if not refresh_cookie:
        _clear_auth_cookies(response, settings)
        raise ApiError(401, "REFRESH_INVALID", "Refresh token missing or invalid")

    try:
        claims = decode_token(
            refresh_cookie,
            settings=settings,
            expected_type=TokenType.REFRESH,
        )
    except ApiError:
        _clear_auth_cookies(response, settings)
        raise ApiError(401, "REFRESH_INVALID", "Refresh token missing or invalid")

    if claims.family is None:
        _clear_auth_cookies(response, settings)
        raise ApiError(401, "REFRESH_INVALID", "Refresh token missing or invalid")

    family_row = await session.get(RefreshTokenFamily, claims.family)

    if family_row is None or family_row.revoked_at is not None:
        _clear_auth_cookies(response, settings)
        raise ApiError(401, "REFRESH_INVALID", "Refresh token missing or invalid")

    if claims.jti != family_row.current_jti:
        family_row.revoked_at = datetime.now(UTC)
        await session.commit()
        _clear_auth_cookies(response, settings)
        logger.warning(
            "Refresh failed: user_id=%s reason=family_reuse_detected family=%s",
            claims.user_id,
            claims.family,
        )
        raise ApiError(401, "REFRESH_INVALID", "Refresh token missing or invalid")

    user = await session.get(User, claims.user_id)
    if user is None:
        _clear_auth_cookies(response, settings)
        raise ApiError(401, "REFRESH_INVALID", "Refresh token missing or invalid")

    csrf_token = secrets.token_hex(32)
    new_jti = uuid4()

    cas_result: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
        update(RefreshTokenFamily)
        .where(RefreshTokenFamily.family == family_row.family)
        .where(RefreshTokenFamily.current_jti == claims.jti)
        .where(RefreshTokenFamily.revoked_at.is_(None))
        .values(current_jti=new_jti)
    )
    await session.commit()

    if cas_result.rowcount == 0:
        _clear_auth_cookies(response, settings)
        raise ApiError(401, "REFRESH_INVALID", "Refresh token missing or invalid")

    access_token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=csrf_token,
    )
    new_refresh_token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.REFRESH,
        family=family_row.family,
        jti=new_jti,
    )

    _set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=new_refresh_token,
        csrf_token=csrf_token,
        settings=settings,
    )

    return LoginResponse(user=_to_user_response(user))


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(require_current_user),
) -> UserResponse:
    return _to_user_response(current_user)
