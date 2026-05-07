from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

import bcrypt
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from starlette.requests import Request

from backend.app.config import Settings
from backend.app.core.errors import ApiError
from backend.app.models.enums import UserRole

BCRYPT_ROUNDS = 12
_BCRYPT_MAX_PASSWORD_BYTES = 72


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(slots=True, kw_only=True)
class TokenClaims:
    user_id: UUID
    role: UserRole
    token_type: TokenType
    csrf: str | None
    jti: UUID
    family: UUID | None


def validate_password_length(password: str) -> None:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters long")


def _normalize_password(password: str) -> bytes:
    # bcrypt uses only the first 72 bytes of the password. Truncating
    # explicitly keeps behavior stable across bcrypt versions.
    return password.encode("utf-8")[:_BCRYPT_MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    validate_password_length(password)
    normalized_password = _normalize_password(password)
    hashed_password = bcrypt.hashpw(
        normalized_password,
        bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
    )
    return hashed_password.decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        _normalize_password(password),
        hashed_password.encode("utf-8"),
    )


def create_token(
    *,
    user_id: UUID,
    role: UserRole,
    settings: Settings,
    token_type: TokenType,
    csrf: str | None = None,
    family: UUID | None = None,
    jti: UUID | None = None,
    expires_in_seconds: int | None = None,
) -> str:
    """
    Encode and sign a JWT.

    `jti` — if provided, uses this UUID as the token identifier; otherwise
    one is generated internally. Callers that need to know the jti (e.g. for
    storing in refresh_token_families) should generate it before calling and
    pass it in.
    """
    now = datetime.now(UTC)
    ttl = expires_in_seconds
    if ttl is None:
        ttl = (
            settings.auth.access_token_ttl_seconds
            if token_type is TokenType.ACCESS
            else settings.auth.refresh_token_ttl_seconds
        )

    token_jti = jti if jti is not None else uuid4()

    payload = {
        "sub": str(user_id),
        "role": role.value,
        "csrf": csrf,
        "typ": token_type.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": str(token_jti),
    }
    if family is not None:
        payload["family"] = str(family)

    return jwt.encode(
        payload,
        settings.auth.jwt_secret.get_secret_value(),
        algorithm=settings.auth.jwt_algorithm,
    )


def decode_token(
    token: str,
    *,
    settings: Settings,
    expected_type: TokenType,
) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.auth.jwt_secret.get_secret_value(),
            algorithms=[settings.auth.jwt_algorithm],
        )
    except ExpiredSignatureError as exc:
        raise ApiError(401, "TOKEN_EXPIRED", "Access token expired") from exc
    except InvalidTokenError as exc:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required") from exc

    try:
        token_type = TokenType(payload["typ"])
        if token_type is not expected_type:
            raise ApiError(401, "UNAUTHENTICATED", "Authentication required")

        family = payload.get("family")
        return TokenClaims(
            user_id=UUID(payload["sub"]),
            role=UserRole(payload["role"]),
            token_type=token_type,
            csrf=payload.get("csrf"),
            jti=UUID(payload["jti"]),
            family=UUID(family) if family else None,
        )
    except (KeyError, ValueError) as exc:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required") from exc


def extract_access_token(request: Request, settings: Settings) -> str | None:
    cookie_token = request.cookies.get(settings.auth.access_cookie_name)
    if cookie_token:
        return cookie_token

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token
