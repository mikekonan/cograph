from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.enums import RepoSyncRunStatus, RepoSyncTriggerKind, SyncSchedule
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.pipeline.orchestrator import RepoSyncOrchestrator


def compute_next_sync_at(
    schedule: SyncSchedule,
    *,
    sync_hour_utc: int,
    now: datetime | None = None,
) -> datetime | None:
    resolved_now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)

    if schedule in (SyncSchedule.MANUAL, SyncSchedule.WEBHOOK):
        return None

    if schedule is SyncSchedule.HOURLY:
        return resolved_now.replace(minute=0, second=0) + timedelta(hours=1)

    candidate = resolved_now.replace(hour=sync_hour_utc, minute=0, second=0)
    if schedule is SyncSchedule.DAILY:
        if candidate <= resolved_now:
            candidate += timedelta(days=1)
        return candidate

    days_until_monday = (0 - resolved_now.weekday()) % 7
    candidate += timedelta(days=days_until_monday)
    if candidate <= resolved_now:
        candidate += timedelta(days=7)
    return candidate


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


class RepoSyncScheduleService:
    async def update_repository_schedule(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        sync_schedule: SyncSchedule,
        now: datetime | None = None,
    ) -> Repository:
        repository = await session.get(Repository, repository_id)
        if repository is None:
            raise LookupError(f"Repository not found: {repository_id}")

        resolved_now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
        repository.sync_schedule = sync_schedule
        repository.next_sync_at = compute_next_sync_at(
            sync_schedule,
            sync_hour_utc=repository.sync_hour_utc,
            now=resolved_now,
        )

        if sync_schedule is SyncSchedule.WEBHOOK:
            repository.webhook_secret = repository.webhook_secret or generate_webhook_secret()
        else:
            repository.webhook_secret = None

        if sync_schedule is SyncSchedule.MANUAL:
            await self._cancel_pending_scheduled_runs(
                session=session,
                repository_id=repository_id,
                now=resolved_now,
            )

        await session.commit()
        await session.refresh(repository)
        return repository

    async def _cancel_pending_scheduled_runs(
        self,
        *,
        session: AsyncSession,
        repository_id: UUID,
        now: datetime,
    ) -> None:
        pending_runs = list(
            (
                await session.scalars(
                    select(RepoSyncRun).where(
                        RepoSyncRun.repository_id == repository_id,
                        RepoSyncRun.trigger_kind == RepoSyncTriggerKind.SCHEDULE,
                        RepoSyncRun.status == RepoSyncRunStatus.QUEUED,
                    )
                )
            ).all()
        )
        for sync_run in pending_runs:
            sync_run.status = RepoSyncRunStatus.CANCELLED
            sync_run.finished_at = now
            sync_run.error_code = None
            sync_run.error_msg = "Cancelled because sync schedule was set to manual"


@dataclass(slots=True, kw_only=True)
class RepoSyncSchedulerResult:
    due_repositories: int
    queued_runs: int
    deduplicated_runs: int
    skipped_runs: int
    failed_repositories: int


class RepoSyncScheduler:
    def __init__(self, *, orchestrator: RepoSyncOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def run_tick(
        self,
        *,
        session: AsyncSession,
        now: datetime | None = None,
    ) -> RepoSyncSchedulerResult:
        resolved_now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
        due_repository_ids = list(
            (
                await session.scalars(
                    select(Repository.id)
                    .where(
                        Repository.sync_schedule.in_(
                            (
                                SyncSchedule.HOURLY,
                                SyncSchedule.DAILY,
                                SyncSchedule.WEEKLY,
                            )
                        ),
                        Repository.next_sync_at.is_not(None),
                        Repository.next_sync_at <= resolved_now,
                    )
                    .order_by(Repository.next_sync_at.asc(), Repository.created_at.asc())
                )
            ).all()
        )

        queued_runs = 0
        deduplicated_runs = 0
        skipped_runs = 0
        failed_repositories = 0

        for repository_id in due_repository_ids:
            try:
                enqueue_result = await self._orchestrator.enqueue_repository_sync(
                    session=session,
                    repository_id=repository_id,
                    trigger_kind=RepoSyncTriggerKind.SCHEDULE,
                )
            except Exception:
                await session.rollback()
                failed_repositories += 1
                continue

            repository = await session.get(Repository, repository_id)
            assert repository is not None
            repository.next_sync_at = compute_next_sync_at(
                repository.sync_schedule,
                sync_hour_utc=repository.sync_hour_utc,
                now=resolved_now,
            )
            await session.commit()

            if enqueue_result.status is RepoSyncRunStatus.SKIPPED:
                skipped_runs += 1
            elif enqueue_result.deduplicated:
                deduplicated_runs += 1
            else:
                queued_runs += 1

        return RepoSyncSchedulerResult(
            due_repositories=len(due_repository_ids),
            queued_runs=queued_runs,
            deduplicated_runs=deduplicated_runs,
            skipped_runs=skipped_runs,
            failed_repositories=failed_repositories,
        )
