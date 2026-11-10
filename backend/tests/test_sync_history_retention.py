"""Retention cron: `prune_sync_history` drops finished sync history past 60 days."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    SyncBatchKind,
    SyncBatchTrigger,
    SyncJobStatus,
    SyncStep,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.pipeline import worker


async def test_prune_sync_history_keeps_recent_and_active(
    app, db_session, monkeypatch
) -> None:
    # One row per chunk, so the loop has to come back for more.
    monkeypatch.setattr(worker, "_PRUNE_CHUNK", 1)
    repo = Repository(
        host="example.com",
        git_url="https://example.com/acme/depot.git",
        name="depot",
        owner="acme",
        branch="main",
    )
    db_session.add(repo)
    await db_session.commit()

    old = datetime.now(UTC) - timedelta(days=61)
    now = datetime.now(UTC)
    runs = {}
    for label, run_status, batch_status, created_at in [
        ("old_done", RepoSyncRunStatus.SKIPPED, SyncJobStatus.SUCCESS, old),
        ("old_error", RepoSyncRunStatus.ERROR, SyncJobStatus.ERROR, old),
        ("old_active", RepoSyncRunStatus.RUNNING, SyncJobStatus.RUNNING, old),
        ("fresh", RepoSyncRunStatus.SUCCESS, SyncJobStatus.SUCCESS, now),
    ]:
        run = RepoSyncRun(
            repository_id=repo.id,
            trigger_kind=RepoSyncTriggerKind.SCHEDULE,
            status=run_status,
            created_at=created_at,
        )
        batch = SyncBatch(
            kind=SyncBatchKind.REPO_SYNC,
            trigger=SyncBatchTrigger.SCHEDULE,
            label="acme/depot",
            repository_id=repo.id,
            run=run,
            status=batch_status,
            created_at=created_at,
        )
        batch.jobs.append(
            SyncJob(repository_id=repo.id, step=SyncStep.CLONE, status=batch_status)
        )
        db_session.add_all([run, batch])
        runs[label] = run
    await db_session.commit()
    kept = {runs["old_active"].id, runs["fresh"].id}

    result = await worker.prune_sync_history(
        {"session_manager": app.state.session_manager}
    )

    assert result == {"batches": 2, "runs": 2, "login_states": 0}
    db_session.expire_all()
    left = set(await db_session.scalars(select(RepoSyncRun.id)))
    assert left == kept
    assert await db_session.scalar(select(func.count()).select_from(SyncJob)) == 2
