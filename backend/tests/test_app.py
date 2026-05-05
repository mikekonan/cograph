from __future__ import annotations

from builtins import ExceptionGroup
from contextlib import asynccontextmanager
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.config import (
    AuthSettings,
    CorsSettings,
    DatabaseSettings,
    EmbeddingSettings,
    Environment,
    GitSettings,
    RedisSettings,
    Settings,
)
from backend.app.core.auth import TokenType, create_token
from backend.app.core.rate_limit import InMemoryRateLimiter
from backend.app.main import create_app
from backend.app.models.enums import UserRole
from backend.app.models.user import User


class _FakeMCPServerSessionManager:
    def __init__(self) -> None:
        self.enter_count = 0
        self.exit_count = 0

    @asynccontextmanager
    async def run(self):
        self.enter_count += 1
        try:
            yield
        finally:
            self.exit_count += 1


class _FakeMCPServer:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            transport_security=SimpleNamespace(
                enable_dns_rebinding_protection=False,
            )
        )
        self.session_manager = _FakeMCPServerSessionManager()

    def streamable_http_app(self):
        async def app(scope, receive, send):
            del receive
            assert scope["type"] == "http"
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"fake-mcp",
                }
            )

        return app


def _make_settings(tmp_path, environment: Environment) -> Settings:
    return Settings(
        environment=environment,
        database=DatabaseSettings(
            url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            echo=False,
        ),
        redis=RedisSettings(url="redis://localhost:6379/15"),
        git=GitSettings(checkouts_root=tmp_path / "checkouts"),
        auth=AuthSettings(
            jwt_secret="test-secret-for-production-tests-32-chars",
            secure_cookies=False,
            registration_enabled=False,
            public_read=True,
        ),
        cors=CorsSettings(allowed_origins=[]),
        embedding=EmbeddingSettings(
            enabled=True,
            api_key="test-embed-key",
        ),
    )


@pytest.mark.anyio
async def test_rate_limiter_in_dev_skips_redis(tmp_path):
    """In DEVELOPMENT env the lifespan must NOT attempt a Redis connection."""
    settings = _make_settings(tmp_path, Environment.DEVELOPMENT)
    app = create_app(settings)

    with patch("redis.asyncio.from_url") as mock_from_url:
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.rate_limiter, InMemoryRateLimiter)
        mock_from_url.assert_not_called()


@pytest.mark.anyio
async def test_rate_limiter_in_production_requires_redis(tmp_path):
    """Production must fail closed when Redis rate limiting is unavailable."""
    settings = _make_settings(tmp_path, Environment.PRODUCTION)
    app = create_app(settings)

    mock_redis = AsyncMock()
    mock_redis.ping.side_effect = ConnectionError("Connection refused")
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        with pytest.raises(ExceptionGroup) as exc_info:
            async with app.router.lifespan_context(app):
                pass
    assert any(
        isinstance(exc, RuntimeError) and "Redis is required" in str(exc)
        for exc in exc_info.value.exceptions
    )


@pytest.mark.anyio
async def test_rate_limiter_in_production_allows_explicit_memory_fallback(
    tmp_path,
    caplog,
):
    settings = _make_settings(tmp_path, Environment.PRODUCTION)
    settings.redis.allow_in_memory_rate_limit_fallback = True
    app = create_app(settings)

    mock_redis = AsyncMock()
    mock_redis.ping.side_effect = ConnectionError("Connection refused")
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        with caplog.at_level(logging.WARNING, logger="backend.app.main"):
            async with app.router.lifespan_context(app):
                assert isinstance(app.state.rate_limiter, InMemoryRateLimiter)

    assert any("Redis unavailable" in r.message for r in caplog.records)


def test_production_rejects_default_or_short_jwt_secret(tmp_path):
    base = {
        "environment": Environment.PRODUCTION,
        "database": DatabaseSettings(url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"),
        "git": GitSettings(checkouts_root=tmp_path / "checkouts"),
    }

    with pytest.raises(ValueError, match="production requires auth.jwt_secret"):
        Settings(**base, auth=AuthSettings(jwt_secret="dev-secret-change-me"))

    with pytest.raises(ValueError, match="production requires auth.jwt_secret"):
        Settings(**base, auth=AuthSettings(jwt_secret="too-short"))

    settings = Settings(
        **base,
        auth=AuthSettings(jwt_secret="a-production-secret-with-32-chars"),
    )
    assert settings.environment is Environment.PRODUCTION


def test_auth_settings_default_to_private_read() -> None:
    assert AuthSettings().public_read is False


async def test_health_reports_connected_database(client):
    response = await client.get("/health", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"
    assert response.json() == {
        "status": "healthy",
        "database": "connected",
        "version": "0.1.0",
    }


async def test_mounted_mcp_disables_direct_host_validation(app):
    assert app.state.mcp_server.settings.transport_security is not None
    assert (
        app.state.mcp_server.settings.transport_security.enable_dns_rebinding_protection
        is False
    )


@pytest.mark.anyio
async def test_mounted_mcp_lifespan_starts_and_stops_session_manager(tmp_path):
    settings = _make_settings(tmp_path, Environment.TESTING)
    fake_server = _FakeMCPServer()

    with patch("backend.app.main.create_mcp_server", return_value=fake_server):
        app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert app.state.mcp_server is fake_server
        assert fake_server.session_manager.enter_count == 1
        assert fake_server.session_manager.exit_count == 0

    assert fake_server.session_manager.exit_count == 1


async def test_auth_config_exposes_frontend_flags(client):
    response = await client.get("/api/auth/config")

    assert response.status_code == 200
    data = response.json()
    assert data["registration_enabled"] is False
    assert data["public_read"] is True
    assert data["providers"] == [
        {
            "kind": "password",
            "slug": None,
            "display_name": None,
            "login_url": None,
            "enabled": True,
        }
    ]
    # needs_bootstrap is present and is a bool (value depends on DB state).
    assert isinstance(data["needs_bootstrap"], bool)
    # capabilities is no longer surfaced — frontend reads role assignments
    # from /admin/llm-runtime instead.
    assert "capabilities" not in data


async def test_register_endpoint_is_disabled(client):
    response = await client.post("/api/auth/register")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_auth_me_rejects_anonymous_requests(client):
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_auth_me_returns_authenticated_admin(client, db_session, settings):
    user = User(
        email="admin@example.com",
        password_hash="hashed",
        name="Admin",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.commit()

    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf="csrf-token",
    )

    client.cookies.set(settings.auth.access_cookie_name, token)
    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"
    assert response.json()["role"] == "admin"
