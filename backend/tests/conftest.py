from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace
from unittest.mock import patch

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient

import backend.app.models  # noqa: F401
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
from backend.app.db.base import Base
from backend.app.main import create_app


@pytest.fixture(autouse=True)
def _restore_backend_logger_propagation():
    """Ensure caplog can capture `backend.*` logs even if a previous test
    ran `_configure_backend_logging` (worker setup), which sets
    `propagate=False` on the `backend` logger and breaks pytest's caplog
    fixture for the rest of the process.
    """
    backend_logger = logging.getLogger("backend")
    backend_logger.propagate = True
    yield

_GRAPH_FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "repos"
_GO_TYPES_FIXTURE_ROOT = _GRAPH_FIXTURES_ROOT / "go_types"
_GO_TYPES_MODULE_PATH = "github.com/mikekonan/go-types/v2"


class AsyncTestClient:
    def __init__(self, client: TestClient) -> None:
        self._client = client
        self._transport = SimpleNamespace(app=client.app)
        self.cookies = client.cookies
        self.headers = client.headers
        self.base_url = client.base_url

    async def request(self, method: str, url: str, **kwargs):
        return await anyio.to_thread.run_sync(
            lambda: self._client.request(method, url, **kwargs)
        )

    async def get(self, url: str, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs):
        return await self.request("POST", url, **kwargs)

    async def patch(self, url: str, **kwargs):
        return await self.request("PATCH", url, **kwargs)

    async def put(self, url: str, **kwargs):
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs):
        return await self.request("DELETE", url, **kwargs)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        environment=Environment.TESTING,
        database=DatabaseSettings(
            url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            echo=False,
        ),
        redis=RedisSettings(url="redis://localhost:6379/15"),
        git=GitSettings(checkouts_root=tmp_path / "checkouts"),
        auth=AuthSettings(
            jwt_secret="test-secret",
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


async def _seed_default_embedding_role(session_manager, settings: Settings) -> None:
    """Seed a default embedding role assignment for tests.

    Phase 30.7 removed the static settings fallback for the embedding role,
    so endpoints that depend on `build_runtime_providers` now return 503 when
    no `llm_model_assignments` row exists. Most tests don't care about the
    runtime resolution logic — they just need *some* embedding provider to
    exist so the dependency resolves. Tests that exercise the
    `EMBEDDING_PROVIDER_REQUIRED` path can mark themselves with
    `pytest.mark.no_default_embedding_role` (handled by autouse below).
    """
    from backend.app.admin.secret_service import SecretCipher
    from backend.app.models.llm_model_assignment import LLMModelAssignment
    from backend.app.models.llm_secret import LLMSecret

    async with session_manager.session() as session:
        secret = LLMSecret(
            name="default-test-secret",
            api_url="https://api.openai.com/v1",
            api_key_encrypted=SecretCipher(settings).encrypt("test-key"),
        )
        session.add(secret)
        await session.flush()
        session.add(
            LLMModelAssignment(
                role="embedding",
                secret_id=secret.id,
                model_name="text-embedding-3-small",
                embedding_dim=1536,
            )
        )
        await session.commit()


@pytest.fixture
async def app(request, settings: Settings) -> AsyncIterator:
    app = create_app(settings)

    async def _fake_embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dimensions for _ in texts]

    skip_default_embedding = request.node.get_closest_marker(
        "no_default_embedding_role"
    )

    # TestClient keeps ASGI lifespan startup/shutdown inside one AnyIO portal.
    # That matches the mounted MCP session-manager lifecycle requirements while
    # the async fixture below proxies requests through the same portal.
    with patch("backend.app.llm.embedder.OpenAIEmbedProvider.embed", new=_fake_embed):
        with TestClient(app) as test_client:
            app.state._test_client = test_client
            async with app.state.session_manager.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            if skip_default_embedding is None:
                await _seed_default_embedding_role(
                    app.state.session_manager, settings
                )
            yield app
            async with app.state.session_manager.engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)


def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers",
        "no_default_embedding_role: skip seeding default embedding LLM role assignment",
    )


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncTestClient]:
    yield AsyncTestClient(app.state._test_client)


@pytest.fixture
async def db_session(app) -> AsyncIterator[AsyncSession]:
    async with app.state.session_manager.session() as session:
        yield session


@pytest.fixture
def go_types_fixture_root() -> Path:
    return _GO_TYPES_FIXTURE_ROOT


@pytest.fixture
def go_types_fixture_module_path() -> str:
    return _GO_TYPES_MODULE_PATH


@pytest.fixture
def copy_go_types_fixture(
    go_types_fixture_root: Path,
) -> Callable[[Path], Path]:
    def _copy(destination: Path) -> Path:
        copytree(go_types_fixture_root, destination)
        return destination

    return _copy
