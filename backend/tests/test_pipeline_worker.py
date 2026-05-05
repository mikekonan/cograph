from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select

from backend.app.admin.secret_service import SecretCipher
from backend.app.db.base import Base
from backend.app.db.session import SessionManager
from backend.app.models.enums import RepoSyncRunStatus, RepositoryStatus, SyncSchedule
from backend.app.models.llm_model_assignment import LLMModelAssignment
from backend.app.models.llm_secret import LLMSecret
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.pipeline.worker import run_repo_sync, worker_shutdown, worker_startup


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
