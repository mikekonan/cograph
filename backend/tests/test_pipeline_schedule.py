from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.models.enums import RepoSyncRunStatus, RepoSyncTriggerKind, SyncSchedule
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.pipeline.orchestrator import RepoSyncEnqueueResult
from backend.app.pipeline.schedule import RepoSyncScheduleService, RepoSyncScheduler, compute_next_sync_at


@dataclass(slots=True)
class _SchedulerOrchestratorResult:
    result: RepoSyncEnqueueResult


class _RecordingSchedulerOrchestrator:
    def __init__(self, results: list[RepoSyncEnqueueResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    async def enqueue_repository_sync(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


def test_compute_next_sync_at_respects_schedule_shape():
    now = datetime(2026, 4, 18, 10, 45, 30, tzinfo=UTC)

    assert compute_next_sync_at(
        SyncSchedule.MANUAL,
        sync_hour_utc=2,
        now=now,
    ) is None
    assert compute_next_sync_at(
        SyncSchedule.WEBHOOK,
        sync_hour_utc=2,
        now=now,
    ) is None
    assert compute_next_sync_at(
        SyncSchedule.HOURLY,
        sync_hour_utc=2,
        now=now,
    ) == datetime(2026, 4, 18, 11, 0, 0, tzinfo=UTC)
    assert compute_next_sync_at(
        SyncSchedule.DAILY,
        sync_hour_utc=12,
        now=now,
    ) == datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
    assert compute_next_sync_at(
        SyncSchedule.WEEKLY,
        sync_hour_utc=9,
        now=now,
    ) == datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)


async def test_schedule_service_generates_webhook_secret_and_clears_next_sync_at(db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.DAILY,
        next_sync_at=datetime(2026, 4, 19, 2, 0, 0, tzinfo=UTC),
    )
    db_session.add(repository)
    await db_session.commit()

    updated_repository = await RepoSyncScheduleService().update_repository_schedule(
        session=db_session,
        repository_id=repository.id,
        sync_schedule=SyncSchedule.WEBHOOK,
        now=datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC),
    )

    assert updated_repository.sync_schedule is SyncSchedule.WEBHOOK
    assert updated_repository.next_sync_at is None
    assert updated_repository.webhook_secret is not None


async def test_schedule_service_cancels_pending_scheduled_runs_when_switching_to_manual(
    db_session,
):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.HOURLY,
        next_sync_at=datetime(2026, 4, 18, 13, 0, 0, tzinfo=UTC),
    )
    db_session.add(repository)
    await db_session.flush()

    cancelled_candidate = RepoSyncRun(
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.SCHEDULE,
        status=RepoSyncRunStatus.QUEUED,
        arq_job_id="job-1",
    )
    untouched_manual_run = RepoSyncRun(
        repository_id=repository.id,
        trigger_kind=RepoSyncTriggerKind.MANUAL,
        status=RepoSyncRunStatus.QUEUED,
        arq_job_id="job-2",
    )
    db_session.add_all([cancelled_candidate, untouched_manual_run])
    await db_session.commit()

    updated_repository = await RepoSyncScheduleService().update_repository_schedule(
        session=db_session,
        repository_id=repository.id,
        sync_schedule=SyncSchedule.MANUAL,
        now=datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC),
    )

    cancelled_candidate = await db_session.get(RepoSyncRun, cancelled_candidate.id)
    untouched_manual_run = await db_session.get(RepoSyncRun, untouched_manual_run.id)

    assert cancelled_candidate is not None
    assert untouched_manual_run is not None
    assert updated_repository.sync_schedule is SyncSchedule.MANUAL
    assert updated_repository.next_sync_at is None
    assert updated_repository.webhook_secret is None
    assert cancelled_candidate.status is RepoSyncRunStatus.CANCELLED
    assert cancelled_candidate.finished_at is not None
    assert untouched_manual_run.status is RepoSyncRunStatus.QUEUED


async def test_scheduler_tick_enqueues_due_repository_and_advances_next_sync_at(db_session):
    now = datetime(2026, 4, 18, 10, 15, 0, tzinfo=UTC)
    due_repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/due.git",
        name="due",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.HOURLY,
        next_sync_at=now - timedelta(minutes=5),
    )
    future_repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/future.git",
        name="future",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.HOURLY,
        next_sync_at=now + timedelta(minutes=5),
    )
    db_session.add_all([due_repository, future_repository])
    await db_session.commit()

    orchestrator = _RecordingSchedulerOrchestrator(
        [
            RepoSyncEnqueueResult(
                repository_id=due_repository.id,
                sync_run_id=uuid4(),
                batch_id=None,
                status=RepoSyncRunStatus.QUEUED,
                requested_ref="abc123",
                deduplicated=False,
            )
        ]
    )
    scheduler = RepoSyncScheduler(orchestrator=orchestrator)

    result = await scheduler.run_tick(session=db_session, now=now)

    refreshed_due_repository = await db_session.get(Repository, due_repository.id)
    refreshed_future_repository = await db_session.get(Repository, future_repository.id)

    assert refreshed_due_repository is not None
    assert refreshed_future_repository is not None
    assert result.due_repositories == 1
    assert result.queued_runs == 1
    assert result.deduplicated_runs == 0
    assert result.skipped_runs == 0
    assert result.failed_repositories == 0
    assert len(orchestrator.calls) == 1
    assert orchestrator.calls[0]["repository_id"] == due_repository.id
    assert orchestrator.calls[0]["trigger_kind"] is RepoSyncTriggerKind.SCHEDULE
    assert refreshed_due_repository.next_sync_at == datetime(2026, 4, 18, 11, 0, 0, tzinfo=UTC)
    assert refreshed_future_repository.next_sync_at == now + timedelta(minutes=5)
