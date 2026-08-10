"""Integration tests for Phase 7b chunk embedding writers.

These tests use the sqlite+aiosqlite fixture (VectorType falls back to TEXT/JSON)
so they run without a live Postgres / pgvector server.

Assertions:
- After a repo pipeline run with RepoDocumentEmbedderService wired in,
  all repo_document_chunks.embedding columns are NOT NULL.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repo_document import RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.pipeline.processor import RepoSyncProcessor


def _write_checkout_with_docs(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "service.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    docs = checkout / "docs"
    docs.mkdir()
    (docs / "overview.md").write_text(
        "# Overview\n\nThis service greets users by name.\n\n## Usage\n\nCall `greet(name)` to get a greeting.\n",
        encoding="utf-8",
    )
    return checkout


async def _make_repo(db_session) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="git@github.com:test/test.git",
        name="test",
        owner="test",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


@pytest.mark.asyncio
async def test_repo_pipeline_embeds_repo_document_chunks(db_session, tmp_path):
    """After a full pipeline run, all repo_document_chunks have embeddings."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout_with_docs(tmp_path)

    provider = FakeEmbedProvider(dims=8)
    processor = RepoSyncProcessor(
        code_embedder_service=CodeEmbedderService(provider, batch_size=128),
        repo_document_embedder_service=RepoDocumentEmbedderService(provider, batch_size=128),
    )

    result = await processor.process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )

    assert result.repo_doc_embed_result is not None
    assert result.repo_doc_embed_result.embedded_nodes > 0

    null_count = await db_session.scalar(
        select(func.count())
        .select_from(RepoDocumentChunk)
        .where(RepoDocumentChunk.embedding.is_(None))
    )
    assert null_count == 0, "All repo_document_chunks must have embeddings after pipeline"


@pytest.mark.asyncio
async def test_repo_pipeline_skips_on_second_run(db_session, tmp_path):
    """Second pipeline run with same content skips all repo doc chunks."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout_with_docs(tmp_path)

    provider = FakeEmbedProvider(dims=8)
    processor = RepoSyncProcessor(
        code_embedder_service=CodeEmbedderService(provider, batch_size=128),
        repo_document_embedder_service=RepoDocumentEmbedderService(provider, batch_size=128),
    )

    first = await processor.process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )
    assert first.repo_doc_embed_result is not None
    first_embedded = first.repo_doc_embed_result.embedded_nodes

    second = await processor.process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )
    assert second.repo_doc_embed_result is not None
    assert second.repo_doc_embed_result.embedded_nodes == 0
    assert second.repo_doc_embed_result.skipped_nodes == first_embedded


