from __future__ import annotations

from pathlib import Path

import pytest
from arq import create_pool
from arq.worker import Worker
from git import Actor, Repo
from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token
from backend.app.models.code_node import CodeNode
from backend.app.models.repo_document import RepoDocument
from backend.app.models.enums import RepoSyncRunStatus, RepositoryStatus, UserRole
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.pipeline.worker import (
    REPO_SYNC_QUEUE_NAME,
    build_redis_settings,
    run_repo_sync,
    worker_shutdown,
    worker_startup,
)

_ACTOR = Actor("Cograph Tests", "tests@example.com")


def _init_source_repo(repo_path: Path) -> Repo:
    repo = Repo.init(repo_path)
    repo.git.checkout("-B", "main")
    return repo


def _commit_file(
    repo: Repo,
    repo_path: Path,
    relative_path: str,
    content: str,
) -> str:
    target_path = repo_path / relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    repo.index.add([relative_path])
    return repo.index.commit("update", author=_ACTOR, committer=_ACTOR).hexsha


@pytest.mark.filterwarnings(
    "ignore:Call to deprecated close. \\(Use aclose\\(\\) instead\\):DeprecationWarning"
)
async def test_live_repo_reindex_api_enqueues_and_processes_sync_job(
    integration_client,
    integration_session_manager,
    integration_settings,
    tmp_path,
):
    source_repo_path = tmp_path / "source"
    source_repo = _init_source_repo(source_repo_path)
    head_commit = _commit_file(
        source_repo,
        source_repo_path,
        "service.py",
        "def helper() -> str:\n    return 'ready'\n",
    )
    head_commit = _commit_file(
        source_repo,
        source_repo_path,
        "README.md",
        "# Demo\n\nUse `helper`.\n",
    )

    async with integration_session_manager.session() as session:
        admin = User(
            email="admin@example.com",
            password_hash="hashed",
            role=UserRole.ADMIN,
        )
        repository = Repository(
            git_url=str(source_repo_path),
            host="example.com",
            name="demo",
            owner="acme",
            branch="main",
        )
        session.add_all([admin, repository])
        await session.commit()
        repository_id = repository.id
        admin_id = admin.id

    token = create_token(
        user_id=admin_id,
        role=UserRole.ADMIN,
        settings=integration_settings,
        token_type=TokenType.ACCESS,
        csrf="csrf-token",
    )
    integration_client.cookies.set(integration_settings.auth.access_cookie_name, token)
    integration_client.headers["X-CSRF-Token"] = "csrf-token"

    first_response = await integration_client.post(
        f"/api/repos/{repository_id}/reindex"
    )
    second_response = await integration_client.post(
        f"/api/repos/{repository_id}/reindex"
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    assert second_response.json()["id"] == first_response.json()["id"]

    async with integration_session_manager.session() as session:
        repository = await session.get(Repository, repository_id)
        sync_runs = list(
            (
                await session.scalars(
                    select(RepoSyncRun).where(
                        RepoSyncRun.repository_id == repository_id
                    )
                )
            ).all()
        )
        assert repository is not None
        assert repository.status is RepositoryStatus.CLONING
        assert len(sync_runs) == 1
        assert sync_runs[0].status is RepoSyncRunStatus.QUEUED
        assert sync_runs[0].requested_ref == head_commit
        assert sync_runs[0].arq_job_id == str(sync_runs[0].id)

    redis_pool = await create_pool(build_redis_settings(integration_settings.redis.url))
    worker = Worker(
        functions=[run_repo_sync],
        queue_name=REPO_SYNC_QUEUE_NAME,
        redis_pool=redis_pool,
        burst=True,
        handle_signals=False,
        poll_delay=0,
        on_startup=worker_startup,
        on_shutdown=worker_shutdown,
        ctx={"settings": integration_settings},
    )
    try:
        await worker.run_check()
    finally:
        await worker.close()
        await redis_pool.aclose()

    async with integration_session_manager.session() as session:
        repository = await session.get(Repository, repository_id)
        sync_run = await session.scalar(
            select(RepoSyncRun).where(RepoSyncRun.repository_id == repository_id)
        )
        code_nodes = list(
            (
                await session.scalars(
                    select(CodeNode).where(CodeNode.repository_id == repository_id)
                )
            ).all()
        )
        repo_documents = list(
            (
                await session.scalars(
                    select(RepoDocument).where(
                        RepoDocument.repository_id == repository_id
                    )
                )
            ).all()
        )
        assert repository is not None
        assert sync_run is not None

        assert repository.status is RepositoryStatus.READY
        assert repository.last_commit == head_commit
        assert repository.last_synced_at is not None
        assert sync_run.status is RepoSyncRunStatus.SUCCESS
        assert sync_run.requested_ref == head_commit
        assert sync_run.started_at is not None
        assert sync_run.finished_at is not None
        assert len(code_nodes) == 2
        assert len(repo_documents) == 1

    documents_response = await integration_client.get(
        f"/api/repos/{repository_id}/documents"
    )
    assert documents_response.status_code == 200
    document_id = documents_response.json()["items"][0]["id"]

    detail_response = await integration_client.get(
        f"/api/repos/{repository_id}/documents/{document_id}"
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["file_path"] == "README.md"
    assert detail_response.json()["chunks"][0]["mentions"][0]["name"] == "helper"
