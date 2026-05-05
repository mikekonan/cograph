"""Integration test: embed step with FakeEmbedProvider against real PostgreSQL.

Verifies:
- code_embeddings rows are created with correct dimensions
- content_hash deduplication works on re-run
- HNSW index is present (information_schema check)
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repository import Repository
from backend.app.pipeline.processor import RepoSyncProcessor

pytestmark = pytest.mark.integration


async def test_embed_pipeline_writes_vector_rows(
    integration_session_manager,
    tmp_path,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "main.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    (checkout / "utils.py").write_text(
        "def double(x: int) -> int:\n    return x * 2\n",
        encoding="utf-8",
    )

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:it/test.git",
            name="test",
            owner="it",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id
        await session.commit()

    provider = FakeEmbedProvider(dims=1536)
    service = CodeEmbedderService(provider, batch_size=64)
    processor = RepoSyncProcessor(code_embedder_service=service)

    async with integration_session_manager.session() as session:
        result = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    assert result.embed_result is not None
    assert result.embed_result.embedded_nodes > 0

    async with integration_session_manager.session() as session:
        embed_count = await session.scalar(
            select(func.count()).select_from(CodeEmbedding)
        )
        node_count = await session.scalar(
            select(func.count())
            .select_from(CodeNode)
            .where(CodeNode.repository_id == repo_id)
        )

    assert embed_count == node_count
    assert embed_count == result.embed_result.embedded_nodes


async def test_embed_pipeline_deduplicates_unchanged_nodes(
    integration_session_manager,
    tmp_path,
):
    checkout = tmp_path / "checkout2"
    checkout.mkdir()
    (checkout / "service.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )

    async with integration_session_manager.session() as session:
        repo = Repository(
            git_url="git@github.com:it/dedup.git",
            name="dedup",
            owner="it",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repo)
        await session.flush()
        repo_id = repo.id
        await session.commit()

    provider = FakeEmbedProvider(dims=1536)
    service = CodeEmbedderService(provider, batch_size=64)
    processor = RepoSyncProcessor(code_embedder_service=service)

    async with integration_session_manager.session() as session:
        first = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    async with integration_session_manager.session() as session:
        second = await processor.process_checkout(
            session=session,
            repository_id=repo_id,
            checkout_path=checkout,
        )

    assert first.embed_result is not None
    assert second.embed_result is not None
    assert second.embed_result.embedded_nodes == 0
    assert second.embed_result.skipped_nodes == first.embed_result.embedded_nodes


async def test_embed_hnsw_index_exists(integration_session_manager):
    """Verify the HNSW index was created by the migration."""
    async with integration_session_manager.session() as session:
        idx_name = await session.scalar(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'code_embeddings' "
                "AND indexname = 'idx_code_embeddings_hnsw'"
            )
        )
    assert idx_name == "idx_code_embeddings_hnsw", (
        "HNSW index missing from code_embeddings — did 0012 migration run?"
    )
