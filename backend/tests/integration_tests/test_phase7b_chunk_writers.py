"""Integration tests for Phase 7b chunk embedding writers.

These tests use the sqlite+aiosqlite fixture (VectorType falls back to TEXT/JSON)
so they run without a live Postgres / pgvector server.

Assertions:
- After a repo pipeline run with RepoDocumentEmbedderService wired in,
  all repo_document_chunks.embedding columns are NOT NULL.
- After a BankIndexer.upsert + BankDocumentEmbedderService.embed_bank call,
  all bank_document_chunks.embedding columns are NOT NULL.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.app.banks.indexer import BankDocumentUpsertInput, BankIndexer
from backend.app.llm.bank_document_embedder import BankDocumentEmbedderService
from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.models.bank import Bank, BankDocumentChunk
from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repo_document import RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.models.user import User
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


@pytest.mark.asyncio
async def test_bank_upload_embeds_chunks(db_session):
    """After upsert + embed_bank, all bank_document_chunks have embeddings."""
    owner = User(email="owner@example.com", password_hash="hashed")
    bank = Bank(name="Docs Bank", owner=owner)
    db_session.add(bank)
    await db_session.flush()

    indexer = BankIndexer()
    await indexer.upsert_document(
        session=db_session,
        bank_id=bank.id,
        document=BankDocumentUpsertInput(
            source_key="adr/ADR-001.md",
            content="# ADR-001\n\nWe chose PostgreSQL.\n\n## Context\n\nPrimary data store.\n",
        ),
    )

    provider = FakeEmbedProvider(dims=8)
    embedder = BankDocumentEmbedderService(provider, batch_size=128)
    embed_result = await embedder.embed_bank(session=db_session, bank_id=bank.id)

    assert embed_result.embedded_nodes > 0
    await db_session.commit()

    null_count = await db_session.scalar(
        select(func.count())
        .select_from(BankDocumentChunk)
        .where(BankDocumentChunk.embedding.is_(None))
    )
    assert null_count == 0, "All bank_document_chunks must have embeddings after embed_bank"


@pytest.mark.asyncio
async def test_bank_embed_skips_on_re_upload_same_content(db_session):
    """Re-uploading the same document skips embedding (all chunks already up to date)."""
    owner = User(email="owner2@example.com", password_hash="hashed")
    bank = Bank(name="Stable Docs", owner=owner)
    db_session.add(bank)
    await db_session.flush()

    content = "# Guide\n\nHello world.\n\n## Details\n\nMore info here.\n"
    indexer = BankIndexer()
    provider = FakeEmbedProvider(dims=8)
    embedder = BankDocumentEmbedderService(provider, batch_size=128)

    # First upload + embed.
    await indexer.upsert_document(
        session=db_session,
        bank_id=bank.id,
        document=BankDocumentUpsertInput(source_key="guide.md", content=content),
    )
    first = await embedder.embed_bank(session=db_session, bank_id=bank.id)
    assert first.embedded_nodes > 0
    await db_session.commit()

    # Second upload (same content) + embed — should skip.
    await indexer.upsert_document(
        session=db_session,
        bank_id=bank.id,
        document=BankDocumentUpsertInput(source_key="guide.md", content=content),
    )
    second = await embedder.embed_bank(session=db_session, bank_id=bank.id)
    assert second.embedded_nodes == 0
    assert second.skipped_nodes == first.embedded_nodes
