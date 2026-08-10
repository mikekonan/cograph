"""Issue Cograph cookie sessions.

Shared between password login (`POST /api/auth/login`) and OIDC callback
(`GET /api/auth/oidc/{slug}/callback`). The two paths used to inline this
sequence; consolidating prevents drift in the cookie set / refresh family
machinery.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.auth import TokenType, create_token
from backend.app.models.refresh_token_family import RefreshTokenFamily
from backend.app.models.user import User


def set_auth_cookies(
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
    response.set_cookie(
        key=settings.auth.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=settings.auth.access_token_ttl_seconds,
    )


async def mint_session_cookies(
    *,
    response: Response,
    session: AsyncSession,
    user: User,
    settings: Settings,
    update_last_login: bool = True,
) -> None:
    """Issue access + refresh + CSRF cookies for `user`.

    Adds a fresh `RefreshTokenFamily` row to the session (caller commits).
    Updates `last_login_at` unless the caller is doing it themselves.
    """
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
    session.add(
        RefreshTokenFamily(
            family=family,
            user_id=user.id,
            current_jti=refresh_jti,
        )
    )
    if update_last_login:
        user.last_login_at = datetime.now(UTC)

    set_auth_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        settings=settings,
    )
