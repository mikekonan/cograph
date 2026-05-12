"""Shared cascade helper for terminating a stuck or cancelled run.

A ``repo_sync_runs`` row drives three downstream rows that must move to
a terminal state together: the run itself, its ``sync_batches`` row, and
the per-step ``sync_jobs``. Two callers need this:

- the stale-sweep cron in :mod:`backend.app.pipeline.stale_sweep`
  (run → ERROR, error_code=``worker_died``);
- the force-cancel API in :mod:`backend.app.pipeline.orchestrator`
  (run → CANCELLED, error_code=``cancelled_by_admin``).

The shapes diverge slightly — the sweep also flips the repository row
to ERROR, while admin cancel leaves it alone — so the helper takes
status enums and an error code/message as parameters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepositoryStatus,
    SyncJobStatus,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob

_log = logging.getLogger(__name__)

# Active states the cascade is allowed to act on.
_ACTIVE_RUN_STATES = (RepoSyncRunStatus.QUEUED, RepoSyncRunStatus.RUNNING)
_ACTIVE_JOB_STATES = (SyncJobStatus.QUEUED, SyncJobStatus.RUNNING)
_ACTIVE_REPO_STATES = (
    RepositoryStatus.INDEXING,
    RepositoryStatus.EMBEDDING,
    RepositoryStatus.GENERATING,
    RepositoryStatus.CLONING,
)

# Width of the timestamp window used to find a legacy batch when
# ``SyncBatch.run_id`` was not backfilled by migration 0051. The
# orchestrator commits run+batch in the same transaction, so 5 s is
# already 100x the real gap.
_LEGACY_BATCH_WINDOW = timedelta(seconds=5)


@dataclass(slots=True, kw_only=True)
class CascadeResult:
    batch_id: UUID | None
    jobs_updated: int
    repository_updated: bool


async def fail_run_cascade(
    *,
    session: AsyncSession,
    run: RepoSyncRun,
    run_status: RepoSyncRunStatus,
    batch_status: SyncJobStatus,
    job_status: SyncJobStatus,
    error_code: str,
    error_msg: str,
) -> CascadeResult:
    """Move ``run`` and its downstream rows to a terminal state.

    Idempotent: if ``run.status`` is no longer active, returns a
    zero-effect ``CascadeResult`` without touching any row.

    The caller is responsible for committing the session — every mutation
    happens on the passed-in session so the helper composes with whatever
    outer transaction the caller has open (sweep batches multiple runs
    per commit; the API endpoint commits a single run).
    """

    # Re-read from DB so a stale row reference (e.g. fetched at the top
    # of the sweep batch) does not bypass the idempotency check.
    await session.refresh(run)
    if run.status not in _ACTIVE_RUN_STATES:
        return CascadeResult(batch_id=None, jobs_updated=0, repository_updated=False)

    finished_at = datetime.now(UTC)
    run.status = run_status
    run.finished_at = finished_at
    run.error_code = error_code
    run.error_msg = error_msg

    batch = await _resolve_batch(session=session, run=run)
    batch_id: UUID | None = None
    if batch is not None:
        batch_id = batch.id
        if batch.status in _ACTIVE_JOB_STATES:
            batch.status = batch_status
            batch.finished_at = finished_at

    jobs_updated = 0
    if batch is not None:
        result = await session.execute(
            update(SyncJob)
            .where(SyncJob.batch_id == batch.id)
            .where(SyncJob.status.in_(_ACTIVE_JOB_STATES))
            .values(
                status=job_status,
                finished_at=finished_at,
                error_code=error_code,
                error_msg=error_msg,
            )
        )
        jobs_updated = result.rowcount or 0

    repository_updated = False
    # Only flip the repository row on the ERROR path. A manual cancel
    # leaves the repo state alone — the operator usually just wants to
    # unblock the dedup, not advertise that the repo is in error.
    if run_status is RepoSyncRunStatus.ERROR:
        repository = await session.get(Repository, run.repository_id)
        if repository is not None and repository.status in _ACTIVE_REPO_STATES:
            repository.status = RepositoryStatus.ERROR
            repository.error_msg = error_msg
            repository_updated = True

    return CascadeResult(
        batch_id=batch_id,
        jobs_updated=jobs_updated,
        repository_updated=repository_updated,
    )


async def _resolve_batch(
    *, session: AsyncSession, run: RepoSyncRun
) -> SyncBatch | None:
    """Find the batch that belongs to ``run``.

    Prefers the explicit ``run_id`` FK (migration 0051). Falls back to a
    repository-id + creation-timestamp window for legacy rows that pre-date
    the migration — both run and batch are created in the same
    transaction so the gap is sub-millisecond in practice; a 5 s window
    is overkill but safe.
    """

    stmt = select(SyncBatch).where(SyncBatch.run_id == run.id)
    batch = (await session.scalars(stmt)).first()
    if batch is not None:
        return batch

    if run.created_at is None:
        return None

    lo = run.created_at - _LEGACY_BATCH_WINDOW
    hi = run.created_at + _LEGACY_BATCH_WINDOW
    stmt = (
        select(SyncBatch)
        .where(SyncBatch.repository_id == run.repository_id)
        .where(SyncBatch.created_at >= lo)
        .where(SyncBatch.created_at <= hi)
        .order_by(SyncBatch.created_at.asc())
        .limit(1)
    )
    batch = (await session.scalars(stmt)).first()
    if batch is not None:
        _log.info(
            "fail_run_cascade fell back to timestamp window for legacy run %s "
            "(batch=%s)",
            run.id,
            batch.id,
        )
    return batch
