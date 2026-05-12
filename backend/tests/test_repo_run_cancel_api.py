"""Tests for ``POST /repos/{slug}/runs/{run_id}/cancel``.

Covers happy paths (QUEUED + RUNNING cancellation, audit row written),
authorization (admin only), and edge cases (already-terminal run, wrong
repository, ARQ abort failure non-fatal, idempotent orchestrator call).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token
from backend.app.core.deps import get_repo_sync_orchestrator
from backend.app.models.audit_event import AuditEvent
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    SyncBatchKind,
    SyncBatchTrigger,
    SyncJobStatus,
    SyncStep,
    UserRole,
)
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.models.user import User
from backend.app.pipeline.orchestrator import RepoSyncOrchestrator


_TEST_CSRF = "csrf-token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _auth_user(client, settings, user: User) -> None:
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


async def _make_user(db_session, *, role: UserRole) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _make_repo(db_session) -> Repository:
    repo = Repository(
        host="example.com",
        git_url=f"https://example.com/acme/r-{uuid4().hex[:6]}.git",
        name=f"r-{uuid4().hex[:6]}",
        owner="acme",
        branch="main",
        status=RepositoryStatus.INDEXING,
    )
    db_session.add(repo)
    await db_session.commit()
    return repo


async def _seed_run_with_batch_and_job(
    db_session,
    *,
    repository_id: UUID,
    status: RepoSyncRunStatus,
    arq_job_id: str | None = None,
) -> tuple[RepoSyncRun, SyncBatch, SyncJob]:
    run = RepoSyncRun(
        repository_id=repository_id,
        trigger_kind=RepoSyncTriggerKind.MANUAL,
        status=status,
        arq_job_id=arq_job_id,
    )
    db_session.add(run)
    await db_session.flush()
    batch = SyncBatch(
        kind=SyncBatchKind.REPO_SYNC,
        trigger=SyncBatchTrigger.MANUAL,
        repository_id=repository_id,
        run_id=run.id,
        status=SyncJobStatus.RUNNING,
    )
    db_session.add(batch)
    await db_session.flush()
    job = SyncJob(
        batch_id=batch.id,
        step=SyncStep.EMBED,
        status=SyncJobStatus.RUNNING,
    )
    db_session.add(job)
    await db_session.commit()
    return run, batch, job


class _RecordingArqAbort:
    """Records ``Job.abort`` calls so tests can assert they happened."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, float]] = []
        self._fail = fail

    def install(self, monkeypatch) -> None:
        outer = self

        class _FakeJob:
            def __init__(self, job_id, redis, _queue_name):  # noqa: ANN001
                self.job_id = job_id

            async def abort(self, timeout: float = 1.0):  # noqa: ANN001
                outer.calls.append((self.job_id, timeout))
                if outer._fail:
                    raise RuntimeError("redis abort failed")
                return True

        monkeypatch.setattr("arq.jobs.Job", _FakeJob)


def _install_orchestrator(app, settings, *, queue=None) -> RepoSyncOrchestrator:
    """Override the orchestrator dependency with one that has a stub queue.

    The endpoint itself uses ``_resolve_repo_sync_orchestrator``; in tests
    the app has no real arq pool, so we plug a dummy.
    """
    from backend.app.pipeline.checkout import GitCheckoutAdapter

    class _NullQueue:
        async def enqueue_job(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("enqueue_job should not be called for cancel tests")

    orchestrator = RepoSyncOrchestrator(
        job_queue=queue or _NullQueue(),
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
        settings=settings,
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator
    return orchestrator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_queued_run_cascades(app, client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repository = await _make_repo(db_session)
    run, batch, job = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.QUEUED,
    )
    _install_orchestrator(app, settings)
    await _auth_user(client, settings, admin)

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/runs/{run.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(run.id)
    assert body["status"] == RepoSyncRunStatus.CANCELLED.value
    await db_session.refresh(run)
    await db_session.refresh(batch)
    await db_session.refresh(job)
    assert run.status is RepoSyncRunStatus.CANCELLED
    assert run.error_code == "cancelled_by_admin"
    assert batch.status is SyncJobStatus.CANCELLED
    assert job.status is SyncJobStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_running_run_invokes_arq_abort(
    app, client, db_session, settings, monkeypatch
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repository = await _make_repo(db_session)
    run, _, _ = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
        arq_job_id=str(uuid4()),
    )
    recorder = _RecordingArqAbort()
    recorder.install(monkeypatch)
    _install_orchestrator(app, settings)
    await _auth_user(client, settings, admin)

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/runs/{run.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 200
    assert len(recorder.calls) == 1
    job_id, timeout = recorder.calls[0]
    assert job_id == run.arq_job_id
    assert timeout == 2.0


@pytest.mark.asyncio
async def test_cancel_already_terminal_run_returns_409(
    app, client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repository = await _make_repo(db_session)
    run, _, _ = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.SUCCESS,
    )
    _install_orchestrator(app, settings)
    await _auth_user(client, settings, admin)

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/runs/{run.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"


@pytest.mark.asyncio
async def test_cancel_run_for_different_repo_returns_404(
    app, client, db_session, settings
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repo_a = await _make_repo(db_session)
    repo_b = await _make_repo(db_session)
    run, _, _ = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repo_a.id,
        status=RepoSyncRunStatus.QUEUED,
    )
    _install_orchestrator(app, settings)
    await _auth_user(client, settings, admin)

    # Slug for repo_b but run belongs to repo_a — must 404, not 409.
    response = await client.post(
        f"/api/repos/{repo_b.host}/{repo_b.owner}/{repo_b.name}"
        f"/runs/{run.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_run_requires_admin(app, client, db_session, settings):
    user = await _make_user(db_session, role=UserRole.USER)
    repository = await _make_repo(db_session)
    run, _, _ = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
    )
    _install_orchestrator(app, settings)
    await _auth_user(client, settings, user)

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/runs/{run.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cancel_run_writes_audit_row(app, client, db_session, settings):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repository = await _make_repo(db_session)
    run, _, _ = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.QUEUED,
    )
    _install_orchestrator(app, settings)
    await _auth_user(client, settings, admin)

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/runs/{run.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 200
    audit = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "repo_sync_run_cancelled"
            )
        )
    ).first()
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.metadata_json["run_id"] == str(run.id)
    assert audit.metadata_json["repository_id"] == str(repository.id)


@pytest.mark.asyncio
async def test_cancel_run_commits_even_when_arq_abort_fails(
    app, client, db_session, settings, monkeypatch
):
    admin = await _make_user(db_session, role=UserRole.ADMIN)
    repository = await _make_repo(db_session)
    run, _, _ = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.RUNNING,
        arq_job_id=str(uuid4()),
    )
    recorder = _RecordingArqAbort(fail=True)
    recorder.install(monkeypatch)
    _install_orchestrator(app, settings)
    await _auth_user(client, settings, admin)

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
        f"/runs/{run.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 200
    await db_session.refresh(run)
    assert run.status is RepoSyncRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_run_is_idempotent_at_orchestrator_level(
    db_session, settings
):
    repository = await _make_repo(db_session)
    run, _, _ = await _seed_run_with_batch_and_job(
        db_session,
        repository_id=repository.id,
        status=RepoSyncRunStatus.CANCELLED,
    )
    from backend.app.pipeline.checkout import GitCheckoutAdapter

    class _NullQueue:
        async def enqueue_job(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    orchestrator = RepoSyncOrchestrator(
        job_queue=_NullQueue(),
        checkout_adapter=GitCheckoutAdapter(checkouts_root=settings.git.checkouts_root),
        settings=settings,
    )

    result = await orchestrator.cancel_run(
        session=db_session,
        run_id=run.id,
        actor_user_id=None,
    )

    assert result.status is RepoSyncRunStatus.CANCELLED
    # No audit row should be written on the no-op path.
    audit = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "repo_sync_run_cancelled"
            )
        )
    ).first()
    assert audit is None
