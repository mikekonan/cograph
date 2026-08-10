"""Tests for POST /api/auth/bootstrap and the needs_bootstrap flag."""

from __future__ import annotations

import re
import secrets

from backend.app.core.bootstrap import (
    BOOTSTRAP_TOKEN_HEX_LENGTH,
    generate_bootstrap_token,
    hash_bootstrap_token,
)
from backend.app.core.auth import hash_password
from backend.app.models.enums import UserRole
from backend.app.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_bootstrap_hash(app, token: str) -> None:
    app.state.bootstrap_token_hash = hash_bootstrap_token(token)


def _clear_bootstrap(app) -> None:
    app.state.bootstrap_token_hash = None


# ---------------------------------------------------------------------------
# GET /api/auth/config — needs_bootstrap field
# ---------------------------------------------------------------------------


def test_generate_bootstrap_token_is_short_copyable_hex() -> None:
    token = generate_bootstrap_token()

    assert len(token) == BOOTSTRAP_TOKEN_HEX_LENGTH
    assert re.fullmatch(r"[0-9a-f]+", token) is not None


async def test_config_needs_bootstrap_true_when_no_admin(client, app):
    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json()["needs_bootstrap"] is True


async def test_config_needs_bootstrap_false_when_admin_exists(client, app, db_session):
    # Simulate the "CLI created admin after startup" flow (issue #8):
    # app still holds a bootstrap token hash, but config must flip immediately.
    assert app.state.bootstrap_token_hash is not None

    user = User(
        email="admin@example.com",
        password_hash=hash_password("admin12345"),
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.commit()

    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json()["needs_bootstrap"] is False
    assert app.state.bootstrap_token_hash is None


async def test_config_needs_bootstrap_false_when_owner_exists(client, app, db_session):
    # Bootstrap creates OWNER, so config must treat owner as setup-complete.
    assert app.state.bootstrap_token_hash is not None

    user = User(
        email="owner@example.com",
        password_hash=hash_password("admin12345"),
        role=UserRole.OWNER,
    )
    db_session.add(user)
    await db_session.commit()

    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json()["needs_bootstrap"] is False
    assert app.state.bootstrap_token_hash is None


# ---------------------------------------------------------------------------
# POST /api/auth/bootstrap — happy path
# ---------------------------------------------------------------------------


async def test_bootstrap_creates_admin_and_sets_cookies(
    client, app, db_session, settings
):
    token = secrets.token_hex(32)
    _set_bootstrap_hash(app, token)

    response = await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": token,
            "email": "admin@example.com",
            "password": "admin12345",
            "name": "Admin",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "admin@example.com"
    # Phase 30.1 — bootstrap creates the singleton OWNER, not a plain admin.
    assert body["user"]["role"] == "owner"
    assert body["user"]["is_owner"] is True

    # All three auth cookies must be set.
    cookie_names = {c.name for c in response.cookies.jar}
    assert settings.auth.access_cookie_name in cookie_names
    assert settings.auth.refresh_cookie_name in cookie_names
    assert settings.auth.csrf_cookie_name in cookie_names


async def test_bootstrap_clears_token_hash_after_success(client, app):
    token = secrets.token_hex(32)
    _set_bootstrap_hash(app, token)

    await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": token,
            "email": "admin@example.com",
            "password": "admin12345",
            "name": "Admin",
        },
    )

    assert app.state.bootstrap_token_hash is None


async def test_bootstrap_needs_bootstrap_false_after_success(client, app):
    token = secrets.token_hex(32)
    _set_bootstrap_hash(app, token)

    await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": token,
            "email": "admin@example.com",
            "password": "admin12345",
            "name": "Admin",
        },
    )

    config_resp = await client.get("/api/auth/config")
    assert config_resp.json()["needs_bootstrap"] is False


# ---------------------------------------------------------------------------
# POST /api/auth/bootstrap — error paths
# ---------------------------------------------------------------------------


async def test_bootstrap_rejects_when_no_token_hash(client, app):
    _clear_bootstrap(app)
    response = await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": "any-token",
            "email": "admin@example.com",
            "password": "admin12345",
            "name": "Admin",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ADMIN_ALREADY_EXISTS"


async def test_bootstrap_rejects_wrong_token(client, app):
    _set_bootstrap_hash(app, "correct-token-value")

    response = await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": "wrong-token-value",
            "email": "admin@example.com",
            "password": "admin12345",
            "name": "Admin",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "BOOTSTRAP_TOKEN_INVALID"


async def test_bootstrap_rejects_short_password(client, app):
    token = secrets.token_hex(32)
    _set_bootstrap_hash(app, token)

    response = await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": token,
            "email": "admin@example.com",
            "password": "short",
            "name": "Admin",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_bootstrap_rejects_invalid_email(client, app):
    """Bootstrap must reject non-email strings for the email field (Task 5)."""
    token = secrets.token_hex(32)
    _set_bootstrap_hash(app, token)

    response = await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": token,
            "email": "admin",  # not a valid email address
            "password": "admin12345",
            "name": "Admin",
        },
    )
    assert response.status_code == 422


async def test_bootstrap_second_call_after_success_returns_409(client, app):
    token = secrets.token_hex(32)
    _set_bootstrap_hash(app, token)

    first = await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": token,
            "email": "admin@example.com",
            "password": "admin12345",
            "name": "Admin",
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/auth/bootstrap",
        json={
            "setup_token": token,
            "email": "admin2@example.com",
            "password": "admin12345",
            "name": "Admin",
        },
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ADMIN_ALREADY_EXISTS"


# ---------------------------------------------------------------------------
# Startup hook — bootstrap_token_hash set when no admin
# ---------------------------------------------------------------------------


async def test_lifespan_sets_bootstrap_hash_when_no_admin(app):
    """After lifespan starts with no admin, bootstrap_token_hash must be set."""
    # The `app` fixture starts the lifespan with an empty DB (no admin).
    assert app.state.bootstrap_token_hash is not None
    # Must be a 64-char hex SHA-256 digest.
    assert len(app.state.bootstrap_token_hash) == 64


async def test_lifespan_does_not_set_bootstrap_hash_when_admin_exists(settings):
    """If an admin already exists, startup must not generate a bootstrap token."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.app.db.base import Base
    from backend.app.main import create_app

    # Seed DB with an admin before app startup.
    engine = create_async_engine(settings.database.url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        session.add(
            User(
                email="admin@example.com",
                password_hash=hash_password("admin12345"),
                role=UserRole.ADMIN,
            )
        )
        await session.commit()
    await engine.dispose()

    app = create_app(settings)
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        assert app.state.bootstrap_token_hash is None
        response = client.get("/api/auth/config")
        assert response.status_code == 200
        assert response.json()["needs_bootstrap"] is False


async def test_lifespan_does_not_set_bootstrap_hash_when_owner_exists(settings):
    """If an owner already exists, startup must not generate a bootstrap token."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.app.db.base import Base
    from backend.app.main import create_app

    engine = create_async_engine(settings.database.url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        session.add(
            User(
                email="owner@example.com",
                password_hash=hash_password("admin12345"),
                role=UserRole.OWNER,
            )
        )
        await session.commit()
    await engine.dispose()

    app = create_app(settings)
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        assert app.state.bootstrap_token_hash is None
        response = client.get("/api/auth/config")
        assert response.status_code == 200
        assert response.json()["needs_bootstrap"] is False
