from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.admin.secret_service import SecretCipher
from backend.app.db.base import Base
from backend.app.db.session import SessionManager
from backend.app.models.enums import (
    MdJobKind,
    MdJobStatus,
    RepoSyncRunStatus,
    RepositoryStatus,
    SyncSchedule,
)
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.models.llm_secret import LLMSecret
from backend.app.models.md_collection import MdCollection, MdJob
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.pipeline.worker import (
    _sweep_stale_md_jobs,
    run_repo_sync,
    worker_shutdown,
    worker_startup,
)


async def _fake_embed(self, texts: list[str]) -> list[list[float]]:
    return [[0.1] * self.dimensions for _ in texts]


async def test_run_repo_sync_worker_processes_checkout(settings, tmp_path):
    session_manager = SessionManager(settings)
    try:
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

        async with session_manager.session() as session:
            repository = Repository(
                host="example.com",
                git_url="git@github.com:mikekonan/cograph.git",
                name="cograph",
                owner="mikekonan",
                branch="main",
                status=RepositoryStatus.PENDING,
                sync_schedule=SyncSchedule.MANUAL,
            )
            session.add(repository)
            await session.commit()
            repository_id = repository.id

        checkout_path = tmp_path / "checkout"
        checkout_path.mkdir()
        (checkout_path / "service.py").write_text(
            "def helper() -> int:\n    return 1\n",
            encoding="utf-8",
        )

        ctx: dict[str, object] = {"settings": settings}
        with patch("backend.app.llm.embedder.OpenAIEmbedProvider.embed", new=_fake_embed):
            await worker_startup(ctx)
            try:
                result = await run_repo_sync(ctx, str(repository_id), str(checkout_path))
            finally:
                await worker_shutdown(ctx)

        assert result["repository_id"] == str(repository_id)
        assert result["status"] == RepoSyncRunStatus.SUCCESS.value
        assert result["processed_files"] == 1
        assert result["inserted_nodes"] == 2
        assert result["indexed_documents"] == 0
        assert result["indexed_chunks"] == 0

        async with session_manager.session() as session:
            persisted_repository = await session.get(Repository, repository_id)
            sync_run = await session.scalar(
                select(RepoSyncRun).where(RepoSyncRun.repository_id == repository_id)
            )

        assert persisted_repository is not None
        assert sync_run is not None
        assert persisted_repository.status is RepositoryStatus.READY
        assert sync_run.status is RepoSyncRunStatus.SUCCESS
    finally:
        async with session_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await session_manager.dispose()


class _FakeArqPool:
    """Minimal arq pool stub recording enqueue_job calls for the sweep test."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, name: str, *args: object) -> None:
        self.enqueued.append((name, args))

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_sweep_stale_md_jobs_requeues_embed_and_marks_upload_error(
    settings, monkeypatch
):
    """Stale running md_jobs older than 4h: embed re-enqueues, upload errors."""
    session_manager = SessionManager(settings)
    fake_pool = _FakeArqPool()

    async def _fake_create_pool(*_args: object, **_kwargs: object) -> _FakeArqPool:
        return fake_pool

    monkeypatch.setattr("backend.app.pipeline.worker.create_pool", _fake_create_pool)

    try:
        async with session_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        cutoff = datetime.now(UTC) - timedelta(hours=5)
        async with session_manager.session() as session:
            collection = MdCollection(
                name="sweep-col",
                description="",
                visibility="private",
                owner_id=None,
            )
            session.add(collection)
            await session.commit()
            await session.refresh(collection)

            stale_embed = MdJob(
                collection_id=collection.id,
                kind=MdJobKind.EMBED,
                status=MdJobStatus.RUNNING,
                result_summary={},
                started_at=cutoff,
            )
            stale_upload = MdJob(
                collection_id=collection.id,
                kind=MdJobKind.UPLOAD,
                status=MdJobStatus.RUNNING,
                result_summary={"total": 5, "processed": 1, "failed": 0},
                started_at=cutoff,
            )
            fresh_embed = MdJob(
                collection_id=collection.id,
                kind=MdJobKind.EMBED,
                status=MdJobStatus.RUNNING,
                result_summary={},
                started_at=datetime.now(UTC) - timedelta(minutes=10),
            )
            session.add_all([stale_embed, stale_upload, fresh_embed])
            await session.commit()
            stale_embed_id = stale_embed.id
            stale_upload_id = stale_upload.id
            fresh_embed_id = fresh_embed.id

        await _sweep_stale_md_jobs(session_manager, settings)

        async with session_manager.session() as session:
            re_embed = await session.get(MdJob, stale_embed_id)
            re_upload = await session.get(MdJob, stale_upload_id)
            untouched = await session.get(MdJob, fresh_embed_id)

        assert re_embed is not None
        assert re_embed.status is MdJobStatus.QUEUED
        assert re_embed.started_at is None

        assert re_upload is not None
        assert re_upload.status is MdJobStatus.ERROR
        assert re_upload.error_message and "abandoned" in re_upload.error_message.lower()

        assert untouched is not None
        assert untouched.status is MdJobStatus.RUNNING

        assert ("embed_md_collection", (str(stale_embed_id).replace("-", ""),)) not in fake_pool.enqueued
        # arq enqueue receives stringified UUIDs in canonical 8-4-4-4-12 form.
        assert any(
            name == "embed_md_collection" and args[1] == str(stale_embed_id)
            for name, args in fake_pool.enqueued
        )
    finally:
        async with session_manager.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await session_manager.dispose()
