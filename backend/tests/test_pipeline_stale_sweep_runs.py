"""Tests for the ``repo_sync_runs`` stale-sweep cron.

The sweep recovers rows orphaned by a dead worker (status=running with
no live ARQ task). Each test sets up a synthetic ``RepoSyncRun`` +
``SyncBatch`` (+ optional ``SyncJob``) tuple and asserts the cascade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.app.db.session import SessionManager
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    SyncBatchKind,
    SyncBatchTrigger,
    SyncJobStatus,
    SyncStep,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.pipeline.stale_sweep import sweep_stale_repo_sync_runs


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeJobStatus:
    """Stand-in for ``arq.jobs.JobStatus`` used to assert the
    ``in_progress`` path. Real value is an enum; for the test we only
    need equality to ``arq.jobs.JobStatus.in_progress``.
    """

    value: str


class _FakeArqPool:
    """Records ``Job(...).status()`` results.

    The sweep imports ``arq.jobs.Job`` lazily, so we monkeypatch
    ``arq.jobs.Job`` to a wrapper that consults this fake.
    """

    def __init__(self, status_by_job_id: dict[str, object]) -> None:
        self.status_by_job_id = status_by_job_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_repo(session) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        status=RepositoryStatus.INDEXING,
    )
    session.add(repo)
    await session.flush()
    return repo


async def _seed_run(
    session,
    *,
    repository_id: UUID,
    status: RepoSyncRunStatus,
    started_at: datetime | None,
    created_at: datetime | None = None,
    arq_job_id: str | None = "job-1",
) -> RepoSyncRun:
    run = RepoSyncRun(
        repository_id=repository_id,
        trigger_kind=RepoSyncTriggerKind.MANUAL,
        status=status,
        arq_job_id=arq_job_id,
        started_at=started_at,
    )
    session.add(run)
    await session.flush()
    if created_at is not None:
        run.created_at = created_at
        await session.flush()
    return run


async def _seed_batch_and_job(
    session,
    *,
    run: RepoSyncRun,
    link_run_id: bool = True,
    batch_created_at: datetime | None = None,
) -> tuple[SyncBatch, SyncJob]:
    batch = SyncBatch(
        kind=SyncBatchKind.REPO_SYNC,
        trigger=SyncBatchTrigger.MANUAL,
        repository_id=run.repository_id,
        run_id=run.id if link_run_id else None,
        status=SyncJobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(batch)
    await session.flush()
    if batch_created_at is not None:
        batch.created_at = batch_created_at
        await session.flush()
    job = SyncJob(
        batch_id=batch.id,
        step=SyncStep.EMBED,
        status=SyncJobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    return batch, job


def _session_manager(app) -> SessionManager:
    return app.state.session_manager


@pytest.fixture
def stale_threshold_minutes(settings) -> int:
    return settings.pipeline_timeouts.stale_run_threshold_minutes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_run_past_threshold_is_failed(
    app, db_session, settings, stale_threshold_minutes
):
    repository = await _seed_repo(db_session)
    started = datetime.now(UTC) - timedelta(minutes=stale_threshold_minutes + 5)
    run = await _seed_run(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
        started_at=started,
    )
    batch, job = await _seed_batch_and_job(db_session, run=run)
    await db_session.commit()

    result = await sweep_stale_repo_sync_runs(
        session_manager=_session_manager(app),
        settings=settings,
        arq_pool=None,
    )

    assert result.runs_failed == 1
    assert result.jobs_cancelled == 1

    await db_session.refresh(run)
    await db_session.refresh(batch)
    await db_session.refresh(job)
    await db_session.refresh(repository)
    assert run.status is RepoSyncRunStatus.ERROR
    assert run.error_code == "worker_died"
    assert batch.status is SyncJobStatus.ERROR
    assert job.status is SyncJobStatus.CANCELLED
    assert repository.status is RepositoryStatus.ERROR


@pytest.mark.asyncio
async def test_queued_run_with_null_started_at_uses_created_at(
    app, db_session, settings, stale_threshold_minutes
):
    repository = await _seed_repo(db_session)
    created = datetime.now(UTC) - timedelta(minutes=stale_threshold_minutes + 1)
    run = await _seed_run(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.QUEUED,
        started_at=None,
        created_at=created,
    )
    await _seed_batch_and_job(db_session, run=run)
    await db_session.commit()

    result = await sweep_stale_repo_sync_runs(
        session_manager=_session_manager(app),
        settings=settings,
        arq_pool=None,
    )

    assert result.runs_failed == 1
    await db_session.refresh(run)
    assert run.status is RepoSyncRunStatus.ERROR


@pytest.mark.asyncio
async def test_fresh_run_under_threshold_is_skipped(
    app, db_session, settings
):
    repository = await _seed_repo(db_session)
    run = await _seed_run(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await _seed_batch_and_job(db_session, run=run)
    await db_session.commit()

    result = await sweep_stale_repo_sync_runs(
        session_manager=_session_manager(app),
        settings=settings,
        arq_pool=None,
    )

    assert result.runs_failed == 0
    await db_session.refresh(run)
    assert run.status is RepoSyncRunStatus.RUNNING


@pytest.mark.asyncio
async def test_arq_in_progress_skips_sweep(
    app, db_session, settings, stale_threshold_minutes, monkeypatch
):
    """Even past threshold, a still-live ARQ job must NOT be reaped.

    This guards against eating a slow but healthy job.
    """
    from arq.jobs import JobStatus

    repository = await _seed_repo(db_session)
    run = await _seed_run(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(minutes=stale_threshold_minutes + 5),
        arq_job_id="live-job",
    )
    await _seed_batch_and_job(db_session, run=run)
    await db_session.commit()

    class _FakeJob:
        def __init__(self, job_id, redis, _queue_name):  # noqa: ANN001
            self.job_id = job_id

        async def status(self):
            return JobStatus.in_progress

    monkeypatch.setattr("arq.jobs.Job", _FakeJob)

    result = await sweep_stale_repo_sync_runs(
        session_manager=_session_manager(app),
        settings=settings,
        arq_pool=object(),  # truthy stand-in; not actually used
    )

    assert result.runs_failed == 0
    await db_session.refresh(run)
    assert run.status is RepoSyncRunStatus.RUNNING


@pytest.mark.asyncio
async def test_arq_not_found_triggers_cascade(
    app, db_session, settings, stale_threshold_minutes, monkeypatch
):
    """ARQ reports the job as ``not_found`` (gone from Redis) → reap."""
    from arq.jobs import JobStatus

    repository = await _seed_repo(db_session)
    run = await _seed_run(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(minutes=stale_threshold_minutes + 1),
    )
    await _seed_batch_and_job(db_session, run=run)
    await db_session.commit()

    class _FakeJob:
        def __init__(self, job_id, redis, _queue_name):  # noqa: ANN001
            self.job_id = job_id

        async def status(self):
            return JobStatus.not_found

    monkeypatch.setattr("arq.jobs.Job", _FakeJob)

    result = await sweep_stale_repo_sync_runs(
        session_manager=_session_manager(app),
        settings=settings,
        arq_pool=object(),
    )

    assert result.runs_failed == 1
    await db_session.refresh(run)
    assert run.status is RepoSyncRunStatus.ERROR


@pytest.mark.asyncio
async def test_sweep_limit_caps_processing(
    app, db_session, settings, stale_threshold_minutes
):
    repository = await _seed_repo(db_session)
    # Seed 6 stale rows, but cap to 2 — exactly 2 should flip.
    object.__setattr__(
        settings.pipeline_timeouts, "stale_run_sweep_limit", 2
    )

    started = datetime.now(UTC) - timedelta(minutes=stale_threshold_minutes + 10)
    for _ in range(6):
        run = await _seed_run(
            db_session,
            repository_id=repository.id,
            status=RepoSyncRunStatus.RUNNING,
            started_at=started,
        )
        await _seed_batch_and_job(db_session, run=run)
    await db_session.commit()

    result = await sweep_stale_repo_sync_runs(
        session_manager=_session_manager(app),
        settings=settings,
        arq_pool=None,
    )

    assert result.runs_failed == 2


@pytest.mark.asyncio
async def test_legacy_batch_with_null_run_id_uses_timestamp_window(
    app, db_session, settings, stale_threshold_minutes
):
    """Pre-migration-0051 rows have ``batch.run_id = NULL``.

    The cascade falls back to a ``repository_id + created_at ± 5 s``
    window, which is what production legacy rows look like.
    """
    repository = await _seed_repo(db_session)
    started = datetime.now(UTC) - timedelta(minutes=stale_threshold_minutes + 5)
    run = await _seed_run(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
        started_at=started,
        created_at=started,
    )
    # Batch with NULL run_id but a created_at within the window.
    batch, job = await _seed_batch_and_job(
        db_session,
        run=run,
        link_run_id=False,
        batch_created_at=started + timedelta(milliseconds=10),
    )
    await db_session.commit()

    result = await sweep_stale_repo_sync_runs(
        session_manager=_session_manager(app),
        settings=settings,
        arq_pool=None,
    )

    assert result.runs_failed == 1
    assert result.jobs_cancelled == 1
    await db_session.refresh(batch)
    await db_session.refresh(job)
    assert batch.status is SyncJobStatus.ERROR
    assert job.status is SyncJobStatus.CANCELLED
