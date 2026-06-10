from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from arq import create_pool
from arq.worker import Worker
from git import Actor, Repo
from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepositoryStatus,
    SyncSchedule,
    UserRole,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.pipeline.worker import (
    REPO_SYNC_QUEUE_NAME,
    build_redis_settings,
    run_repo_sync,
    run_scheduler_tick,
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


async def _run_burst_sync_worker(integration_settings) -> None:
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


@pytest.mark.filterwarnings(
    "ignore:Call to deprecated close. \\(Use aclose\\(\\) instead\\):DeprecationWarning"
)
async def test_live_webhook_flow_enqueues_and_processes_sync_job(
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
            sync_schedule=SyncSchedule.MANUAL,
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

    update_response = await integration_client.patch(
        f"/api/repos/{repository_id}",
        json={"sync_schedule": "webhook"},
    )
    assert update_response.status_code == 200

    webhook_response = await integration_client.get(
        f"/api/admin/repos/{repository_id}/webhook"
    )
    assert webhook_response.status_code == 200
    webhook_secret = webhook_response.json()["webhook_secret"]

    trigger_response = await integration_client.post(
        f"/api/repos/{repository_id}/webhook",
        headers={"X-Cograph-Webhook-Secret": webhook_secret},
    )
    assert trigger_response.status_code == 202
    assert trigger_response.json()["status"] == "pending"

    await _run_burst_sync_worker(integration_settings)

    async with integration_session_manager.session() as session:
        repository = await session.get(Repository, repository_id)
        sync_runs = list(
            (
                await session.scalars(
                    select(RepoSyncRun)
                    .where(RepoSyncRun.repository_id == repository_id)
                    .order_by(RepoSyncRun.created_at.asc())
                )
            ).all()
        )
        code_nodes = list(
            (
                await session.scalars(
                    select(CodeNode).where(CodeNode.repository_id == repository_id)
                )
            ).all()
        )

    assert repository is not None
    assert repository.status is RepositoryStatus.READY
    assert repository.last_commit == head_commit
    assert repository.last_synced_at is not None
    assert len(sync_runs) == 1
    assert sync_runs[0].status is RepoSyncRunStatus.SUCCESS
    assert sync_runs[0].requested_ref == head_commit
    assert len(code_nodes) == 2


@pytest.mark.filterwarnings(
    "ignore:Call to deprecated close. \\(Use aclose\\(\\) instead\\):DeprecationWarning"
)
async def test_live_scheduler_tick_enqueues_due_repo_and_skips_unchanged_head(
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
    now = datetime.now(UTC).replace(microsecond=0)

    async with integration_session_manager.session() as session:
        repository = Repository(
            git_url=str(source_repo_path),
            host="example.com",
            name="demo",
            owner="acme",
            branch="main",
            sync_schedule=SyncSchedule.HOURLY,
            next_sync_at=now - timedelta(minutes=5),
            status=RepositoryStatus.READY,
        )
        session.add(repository)
        await session.commit()
        repository_id = repository.id

    ctx: dict[str, object] = {"settings": integration_settings}
    await worker_startup(ctx)
    try:
        first_tick = await run_scheduler_tick(ctx)
        assert first_tick["due_repositories"] == 1
        assert first_tick["queued_runs"] == 1
        assert first_tick["skipped_runs"] == 0

        await _run_burst_sync_worker(integration_settings)

        async with integration_session_manager.session() as session:
            repository = await session.get(Repository, repository_id)
            assert repository is not None
            assert repository.status is RepositoryStatus.READY
            assert repository.last_commit == head_commit
            repository.next_sync_at = datetime.now(UTC).replace(
                microsecond=0
            ) - timedelta(minutes=5)
            await session.commit()

        second_tick = await run_scheduler_tick(ctx)
        assert second_tick["due_repositories"] == 1
        assert second_tick["queued_runs"] == 0
        assert second_tick["skipped_runs"] == 1
    finally:
        await worker_shutdown(ctx)

    async with integration_session_manager.session() as session:
        repository = await session.get(Repository, repository_id)
        sync_runs = list(
            (
                await session.scalars(
                    select(RepoSyncRun)
                    .where(RepoSyncRun.repository_id == repository_id)
                    .order_by(RepoSyncRun.created_at.asc())
                )
            ).all()
        )

    assert repository is not None
    assert repository.next_sync_at is not None
    assert len(sync_runs) == 2
    assert sync_runs[0].status is RepoSyncRunStatus.SUCCESS
    assert sync_runs[1].status is RepoSyncRunStatus.SKIPPED
    assert sync_runs[1].requested_ref == head_commit
    assert sync_runs[1].arq_job_id is None
