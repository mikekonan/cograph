from __future__ import annotations

from sqlalchemy import select

from backend.app.models.code_node import CodeNode
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.enums import RepoSyncRunStatus, RepositoryStatus, SyncSchedule
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.pipeline.processor import RepoSyncProcessor


async def test_live_postgres_repo_sync_processor_updates_durable_state(
    integration_session_manager,
    tmp_path,
):
    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    (checkout_path / "a.py").write_text(
        "def helper() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (checkout_path / "b.py").write_text(
        "import a\n\ndef call() -> int:\n    return a.helper()\n",
        encoding="utf-8",
    )
    (checkout_path / "README.md").write_text(
        "# Demo\n\nUse `helper` from the service layer.\n",
        encoding="utf-8",
    )

    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url="git@github.com:mikekonan/cograph.git",
            host="example.com",
            name="cograph",
            owner="mikekonan",
            branch="main",
            status=RepositoryStatus.PENDING,
            sync_schedule=SyncSchedule.MANUAL,
        )
        session.add(repository)
        await session.flush()

        result = await RepoSyncProcessor().process_checkout(
            session=session,
            repository_id=repository.id,
            checkout_path=checkout_path,
        )

        persisted_repository = await session.get(Repository, repository.id)
        sync_run = await session.get(RepoSyncRun, result.sync_run_id)
        persisted_nodes = list(
            (
                await session.scalars(
                    select(CodeNode).where(CodeNode.repository_id == repository.id)
                )
            ).all()
        )
        persisted_documents = list(
            (
                await session.scalars(
                    select(RepoDocument).where(
                        RepoDocument.repository_id == repository.id
                    )
                )
            ).all()
        )
        persisted_chunks = list(
            (
                await session.scalars(
                    select(RepoDocumentChunk)
                    .join(RepoDocument)
                    .where(RepoDocument.repository_id == repository.id)
                )
            ).all()
        )

    assert persisted_repository is not None
    assert sync_run is not None
    assert persisted_repository.status is RepositoryStatus.READY
    assert persisted_repository.last_synced_at is not None
    assert sync_run.status is RepoSyncRunStatus.SUCCESS
    assert sync_run.started_at is not None
    assert sync_run.finished_at is not None
    assert len(persisted_nodes) == 4
    assert result.repo_documents is not None
    assert result.repo_documents.indexed_documents == 1
    assert result.repo_documents.indexed_chunks == 1
    assert len(persisted_documents) == 1
    assert len(persisted_chunks) == 1
