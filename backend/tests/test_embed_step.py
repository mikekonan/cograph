"""Tests for the embed pipeline step in RepoSyncProcessor.

Covers:
- embed step runs with FakeEmbedProvider and writes CodeEmbedding rows
- disabled (None) code_embedder_service keeps pipeline green (skipped-step path)
- incremental: unchanged content_hash skips re-embedding
"""

from __future__ import annotations

from sqlalchemy import func, select

from backend.app.llm.code_embedder import CodeEmbedderService
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepositoryStatus,
    SyncSchedule,
    SyncJobStatus,
    SyncStep,
)
from backend.app.models.repository import Repository
from backend.app.models.sync_job import SyncJob
from backend.app.pipeline.processor import RepoSyncProcessor


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


def _write_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "service.py").write_text(
        "def greet(name: str) -> str:\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    return checkout


async def test_embed_step_writes_code_embedding_rows(db_session, tmp_path):
    """Embed step with FakeEmbedProvider inserts one row per code_node."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout(tmp_path)

    provider = FakeEmbedProvider(dims=8)
    service = CodeEmbedderService(provider, batch_size=128)

    result = await RepoSyncProcessor(
        code_embedder_service=service,
    ).process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )

    assert result.status is RepoSyncRunStatus.SUCCESS
    assert result.embed_result is not None
    assert result.embed_result.embedded_nodes > 0

    row_count = await db_session.scalar(select(func.count()).select_from(CodeEmbedding))
    assert row_count == result.embed_result.embedded_nodes


async def test_embed_step_skips_reembedding_unchanged_nodes(db_session, tmp_path):
    """Second run with identical content must skip all nodes (content_hash match)."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout(tmp_path)

    provider = FakeEmbedProvider(dims=8)
    service = CodeEmbedderService(provider, batch_size=128)
    processor = RepoSyncProcessor(code_embedder_service=service)

    # First run — embeds everything.
    first = await processor.process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )
    assert first.embed_result is not None
    first_embedded = first.embed_result.embedded_nodes

    # Second run — same checkout, no file changes.
    second = await processor.process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )
    assert second.embed_result is not None
    assert second.embed_result.embedded_nodes == 0
    assert second.embed_result.skipped_nodes == first_embedded


async def test_embed_step_disabled_keeps_pipeline_green(db_session, tmp_path):
    """When code_embedder_service is None the embed step is skipped and pipeline succeeds."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout(tmp_path)

    # No embedder_service — disabled path.
    result = await RepoSyncProcessor(
        code_embedder_service=None,
    ).process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )

    assert result.status is RepoSyncRunStatus.SUCCESS
    # No embed result and no rows in code_embeddings.
    assert result.embed_result is None
    row_count = await db_session.scalar(select(func.count()).select_from(CodeEmbedding))
    assert row_count == 0
    embed_job = await db_session.scalar(
        select(SyncJob).where(
            SyncJob.repository_id == repo.id,
            SyncJob.step == SyncStep.EMBED,
        )
    )
    assert embed_job is not None
    assert embed_job.status is SyncJobStatus.SKIPPED
    assert embed_job.error_code == "capability_disabled"
    assert (
        embed_job.error_msg
        == "Skipped because the embedding capability was disabled for this run."
    )


async def test_embed_step_regression_disabled_flag(db_session, tmp_path):
    """Regression: disabled flag (None service) must not raise and must leave repo READY."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout(tmp_path)

    result = await RepoSyncProcessor(code_embedder_service=None).process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )

    repo_row = await db_session.get(Repository, repo.id)
    assert repo_row is not None
    assert repo_row.status is RepositoryStatus.READY
    assert result.status is RepoSyncRunStatus.SUCCESS


async def test_embed_result_exposes_model_name(db_session, tmp_path):
    """EmbedResult.model is populated from the provider's model property."""
    repo = await _make_repo(db_session)
    checkout = _write_checkout(tmp_path)

    provider = FakeEmbedProvider(dims=8)
    service = CodeEmbedderService(provider, batch_size=128)

    result = await RepoSyncProcessor(code_embedder_service=service).process_checkout(
        session=db_session,
        repository_id=repo.id,
        checkout_path=checkout,
    )

    assert result.embed_result is not None
    assert result.embed_result.model == "fake-embed-v1"
