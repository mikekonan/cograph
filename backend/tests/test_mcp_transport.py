from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import backend.app.models  # noqa: F401
from backend.app.admin.secret_service import SecretCipher
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
from backend.app.core.auth import hash_password
from backend.app.db.base import Base
from backend.app.db.session import SessionManager
from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.enums import (
    CodeNodeType,
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.models.llm_secret import LLMSecret
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repository import Repository
from backend.app.models.user import User


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", 0))
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EPERM}:
                pytest.skip(
                    "Local environment forbids binding localhost ports; "
                    "skipping transport subprocess smoke."
                )
            raise
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, *, timeout: float = 90.0) -> None:
    # 90s, not 30s: these tests boot a real uvicorn subprocess with the full
    # FastAPI app (backend.app.main). On a busy shared CI runner, cold
    # imports alone can take 30s+ (slowest-20 has seen 33s for the sibling
    # entrypoint test, and `test_mounted_mcp_streamable_http_app_serves_node_tool`
    # has timed out at 30s on at least one CI run). 90s leaves a comfortable
    # margin for transient load spikes without making local runs measurably
    # slower (success path returns as soon as the port is open).
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


def _build_test_settings(tmp_path: Path, *, db_name: str) -> Settings:
    return Settings(
        environment=Environment.TESTING,
        database=DatabaseSettings(
            url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
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


async def _seed_transport_fixture(
    session_manager: SessionManager,
    settings: Settings,
) -> tuple[str, UUID, str]:
    async with session_manager.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

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

    plaintext_token = f"cgr_pat_{secrets.token_urlsafe(36)}"
    token_hash = hashlib.sha256(plaintext_token.encode("utf-8")).digest()

    async with session_manager.session() as session:
        user = User(
            email="mcp-transport@example.com",
            password_hash=hash_password("not-used-here"),
            name="MCP Transport Test",
            role=UserRole.USER,
        )
        session.add(user)
        await session.flush()
        session.add(
            PersonalAccessToken(
                user_id=user.id,
                name="transport-test",
                token_hash=token_hash,
                token_prefix=plaintext_token[:16],
                scopes=["api:read", "api:write", "mcp"],
            )
        )

        repository = Repository(
            host="example.com",
            git_url="git@github.com:mikekonan/cograph.git",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.READY,
            visibility=RepositoryVisibility.PUBLIC,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repository)
        await session.flush()

        node = CodeNode(
            repository_id=repository.id,
            source_file_id=None,
            file_path="svc.py",
            qualified_name="svc.raise_repo_not_ready",
            symbol_key="svc.raise_repo_not_ready",
            node_type=CodeNodeType.FUNCTION,
            name="raise_repo_not_ready",
            language="python",
            start_line=1,
            end_line=5,
            start_byte=None,
            end_byte=None,
            content="def raise_repo_not_ready() -> None:\n    raise RuntimeError('E_REPO_NOT_READY')",
            signature="def raise_repo_not_ready() -> None",
            doc_comment=None,
            summary=None,
            role=None,
            parent_id=None,
            callers=[],
            callees=[],
            node_metadata={},
            content_hash="node-hash",
        )
        session.add(node)
        await session.flush()
        session.add(
            CodeNodeSummary(
                code_node_id=node.id,
                repository_id=repository.id,
                summary="Raises the repo-not-ready guardrail.",
                importance=0.9,
                content_hash="summary-hash",
                neighbor_hash="neighbor-hash",
                model="gpt-4o-mini",
            )
        )
        await session.commit()

    return f"{repository.host}/{repository.owner}/{repository.name}", node.id, plaintext_token


def _build_process_env(repo_root: Path, settings: Settings) -> dict[str, str]:
    return {
        **os.environ,
        "COGRAPH_ENVIRONMENT": "testing",
        "COGRAPH_DATABASE__URL": settings.database.url,
        "COGRAPH_REDIS__URL": settings.redis.url,
        "COGRAPH_GIT__CHECKOUTS_ROOT": str(settings.git.checkouts_root),
        # Match the JWT secret used by the parent process so the subprocess
        # can decrypt LLMSecret rows we seeded — SecretCipher derives its
        # Fernet key from auth.jwt_secret.
        "COGRAPH_AUTH__JWT_SECRET": settings.auth.jwt_secret.get_secret_value(),
        "COGRAPH_AUTH__PUBLIC_READ": str(settings.auth.public_read).lower(),
        "COGRAPH_EMBEDDING__ENABLED": "true",
        "COGRAPH_EMBEDDING__API_URL": settings.embedding.api_url,
        "COGRAPH_EMBEDDING__API_KEY": settings.embedding.api_key.get_secret_value(),
        "COGRAPH_EMBEDDING__MODEL": settings.embedding.model,
        "COGRAPH_EMBEDDING__DIMENSIONS": str(settings.embedding.dimensions),
        "COGRAPH_EMBEDDING__BATCH_SIZE": str(settings.embedding.batch_size),
        "PYTHONPATH": str(repo_root),
    }


async def _call_node_tool(
    base_url: str,
    repository_slug: str,
    node_id: UUID,
    *,
    bearer_token: str | None = None,
    with_summary: bool = True,
) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as http_client:
        async with streamable_http_client(base_url, http_client=http_client) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "cograph_read_node",
                    {
                        "repository": repository_slug,
                        "node_id": str(node_id),
                        "with_summary": with_summary,
                    },
                )
    return json.loads(result.content[0].text)


def _terminate_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        process.wait(timeout=5)


@pytest.mark.asyncio
async def test_mcp_streamable_http_entrypoint_serves_node_tool(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    port = _pick_free_port()
    settings = _build_test_settings(tmp_path, db_name="transport-entrypoint.db")
    session_manager = SessionManager(settings)
    process: subprocess.Popen[str] | None = None
    try:
        repository_slug, node_id, _bearer = await _seed_transport_fixture(session_manager, settings)
        process = subprocess.Popen(
            [
                "python",
                "-m",
                "backend.app.mcp.server",
                "--transport",
                "streamable-http",
                "--port",
                str(port),
            ],
            cwd=repo_root,
            env=_build_process_env(repo_root, settings),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _wait_for_port("127.0.0.1", port)
        except TimeoutError:
            stderr_tail = ""
            if process is not None:
                try:
                    _, stderr_tail = process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    _, stderr_tail = process.communicate()
            raise AssertionError(
                f"MCP entrypoint subprocess never opened port; stderr=\n{stderr_tail}"
            ) from None

        # The standalone entrypoint does NOT mount the per-user auth
        # wrapper (that lives on the FastAPI ASGI app); call without
        # a bearer to validate transport mechanics in isolation.
        payload = await _call_node_tool(
            f"http://127.0.0.1:{port}/mcp",
            repository_slug,
            node_id,
        )

        assert [item["layer"] for item in payload["results"]] == [
            "code",
            "ast",
            "ast_summary",
        ]
        assert (
            payload["results"][2]["snippet"] == "Raises the repo-not-ready guardrail."
        )
    finally:
        _terminate_process(process)
        await session_manager.dispose()


@pytest.mark.asyncio
async def test_mounted_mcp_streamable_http_app_serves_node_tool(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    port = _pick_free_port()
    settings = _build_test_settings(tmp_path, db_name="transport-mounted.db")
    session_manager = SessionManager(settings)
    process: subprocess.Popen[str] | None = None
    try:
        repository_slug, node_id, bearer_token = await _seed_transport_fixture(session_manager, settings)
        process = subprocess.Popen(
            [
                "python",
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=repo_root,
            env=_build_process_env(repo_root, settings),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_port("127.0.0.1", port)

        payload = await _call_node_tool(
            f"http://127.0.0.1:{port}/mcp",
            repository_slug,
            node_id,
            bearer_token=bearer_token,
        )

        assert [item["layer"] for item in payload["results"]] == [
            "code",
            "ast",
            "ast_summary",
        ]
        assert (
            payload["results"][2]["snippet"] == "Raises the repo-not-ready guardrail."
        )
    finally:
        _terminate_process(process)
        await session_manager.dispose()
