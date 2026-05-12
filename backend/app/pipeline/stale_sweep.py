"""Stale-sweep for ``repo_sync_runs`` rows orphaned by a worker death.

When a worker container OOM-kills mid-step it leaves
``repo_sync_runs.status='running'`` behind with no live ARQ task. The
orchestrator then sees the row as active in ``_get_active_sync_run`` and
silently dedupes every subsequent reindex attempt, so the repository
is effectively wedged until an operator runs SQL by hand.

This module is the recovery path. It runs on a cron, finds runs whose
``started_at`` (or ``created_at`` for QUEUED rows that never even
started) is older than the configured threshold, double-checks with the
ARQ queue that no live task is still handling the job, and on a miss
calls :func:`fail_run_cascade` with ``run_status=ERROR`` and
``error_code='worker_died'``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.db.session import SessionManager
from backend.app.models.enums import RepoSyncRunStatus, SyncJobStatus
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME
from backend.app.pipeline.run_cancellation import fail_run_cascade

if TYPE_CHECKING:
    from arq.connections import ArqRedis

_log = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class SweepResult:
    runs_failed: int
    jobs_cancelled: int
    cutoff: datetime


async def sweep_stale_repo_sync_runs(
    *,
    session_manager: SessionManager,
    settings: Settings,
    arq_pool: "ArqRedis | None" = None,
) -> SweepResult:
    """Find and terminate stale ``repo_sync_runs`` rows.

    The ARQ probe is best-effort — if the call raises or the pool isn't
    available, the DB-only path still runs. False positives are guarded
    by the threshold (default 15 min) which is longer than every typical
    step except wiki/summaries, both of which have their own 30-min
    per-step budget already.
    """

    threshold = timedelta(
        minutes=settings.pipeline_timeouts.stale_run_threshold_minutes
    )
    cutoff = datetime.now(UTC) - threshold
    limit = settings.pipeline_timeouts.stale_run_sweep_limit

    runs_failed = 0
    jobs_cancelled = 0

    async with session_manager.session() as session:
        candidates = await _find_stale_runs(
            session=session, cutoff=cutoff, limit=limit
        )
        for run in candidates:
            if await _arq_still_processing(arq_pool, run.arq_job_id):
                continue
            cascade = await fail_run_cascade(
                session=session,
                run=run,
                run_status=RepoSyncRunStatus.ERROR,
                batch_status=SyncJobStatus.ERROR,
                job_status=SyncJobStatus.CANCELLED,
                error_code="worker_died",
                error_msg=(
                    "Worker died mid-run; reaped by stale-sweep "
                    f"(no progress for >={threshold.total_seconds() / 60:.0f} min)."
                ),
            )
            if cascade.batch_id is None and cascade.jobs_updated == 0:
                # Cascade was a no-op (status raced to terminal between
                # SELECT and refresh); don't count it as swept.
                continue
            runs_failed += 1
            jobs_cancelled += cascade.jobs_updated
        await session.commit()

    if runs_failed:
        _log.warning(
            "Stale repo_sync_runs sweep: runs_failed=%d jobs_cancelled=%d cutoff=%s",
            runs_failed,
            jobs_cancelled,
            cutoff.isoformat(),
        )
    else:
        _log.info(
            "Stale repo_sync_runs sweep: no stale rows (cutoff=%s)",
            cutoff.isoformat(),
        )

    return SweepResult(
        runs_failed=runs_failed,
        jobs_cancelled=jobs_cancelled,
        cutoff=cutoff,
    )


async def _find_stale_runs(
    *, session: AsyncSession, cutoff: datetime, limit: int
) -> list[RepoSyncRun]:
    # COALESCE handles QUEUED rows that never moved to RUNNING (started_at
    # is NULL on those) — fall back to created_at so they don't sit
    # forever just because nothing populated started_at.
    age = func.coalesce(RepoSyncRun.started_at, RepoSyncRun.created_at)
    stmt = (
        select(RepoSyncRun)
        .where(
            RepoSyncRun.status.in_(
                (RepoSyncRunStatus.QUEUED, RepoSyncRunStatus.RUNNING)
            )
        )
        .where(age < cutoff)
        .order_by(age.asc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def _arq_still_processing(
    arq_pool: "ArqRedis | None", arq_job_id: str | None
) -> bool:
    """Best-effort ARQ liveness check.

    Returns True only when ARQ confirms the job is still being processed
    by some worker. Any error or unknown status returns False — better
    to occasionally reap a job that ARQ thinks is queued than to leak a
    wedged row forever because the probe failed.
    """

    if arq_pool is None or not arq_job_id:
        return False

    try:
        from arq.jobs import Job, JobStatus

        job = Job(arq_job_id, redis=arq_pool, _queue_name=REPO_SYNC_QUEUE_NAME)
        status = await job.status()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "ARQ liveness probe failed for job %s; falling back to DB-only "
            "cascade. cause=%s",
            arq_job_id,
            exc,
        )
        return False

    return status == JobStatus.in_progress
