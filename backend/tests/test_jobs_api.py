"""Tests for the Jobs API (FE contract §3).

Covers:
- Auth: 401 without cookie, 403 for non-admin
- CSRF: required on mutations
- GET /api/jobs  — all filters (repo_id, step, batch_id, search, status),
                   pagination, sort order
- GET /api/jobs/:id
- GET /api/jobs/batches
- GET /api/jobs/batches/:batch_id
- GET /api/jobs/stats
- POST /api/jobs/:id/retry  — preconditions + happy path
- POST /api/jobs/:id/cancel — preconditions + happy path
- Shape parity against FE_CONTRACT example
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.app.core.auth import TokenType, create_token
from backend.app.models.enums import (
    SyncBatchKind,
    SyncBatchTrigger,
    SyncJobStatus,
    SyncStep,
    UserRole,
)
from backend.app.models.repository import Repository
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.sync_job import SyncJob
from backend.app.models.user import User

_TEST_CSRF = "csrf-token"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def _auth_admin(client, settings, admin: User) -> None:
    token = create_token(
        user_id=admin.id,
        role=admin.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


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


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_repo(suffix: str = "a") -> Repository:
    return Repository(
        host="example.com",
        git_url=f"https://example.com/acme/{suffix}.git",
        name=suffix,
        owner="acme",
        branch="main",
    )


def _make_batch(
    repo: Repository,
    *,
    kind: SyncBatchKind = SyncBatchKind.REPO_SYNC,
    trigger: SyncBatchTrigger = SyncBatchTrigger.MANUAL,
    label: str = "",
    status: SyncJobStatus = SyncJobStatus.QUEUED,
) -> SyncBatch:
    return SyncBatch(
        kind=kind,
        trigger=trigger,
        label=label or f"{repo.owner}/{repo.name}",
        repository_id=repo.id,
        status=status,
    )


def _make_job(
    batch: SyncBatch,
    repo: Repository,
    *,
    step: SyncStep = SyncStep.CLONE,
    status: SyncJobStatus = SyncJobStatus.QUEUED,
    title: str = "",
    progress: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error_code: str | None = None,
    error_msg: str | None = None,
    created_at: datetime | None = None,
) -> SyncJob:
    job = SyncJob(
        batch_id=batch.id,
        repository_id=repo.id,
        step=step,
        title=title or step.value,
        status=status,
        progress=progress,
        started_at=started_at,
        finished_at=finished_at,
        error_code=error_code,
        error_msg=error_msg,
    )
    if created_at is not None:
        job.created_at = created_at
    return job


# ===========================================================================
# GET /api/jobs - auth enforcement (admin-only)
# ===========================================================================


async def test_list_jobs_anonymous_returns_401(client):
    """Unauthenticated requests must be rejected."""
    r = await client.get("/api/jobs")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_get_job_by_id_anonymous_returns_401(client):
    from uuid import uuid4

    r = await client.get(f"/api/jobs/{uuid4()}")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_list_jobs_non_admin_returns_403(client, db_session, settings):
    """Non-admin authenticated users are forbidden from reading jobs."""
    user = User(email="user@example.com", password_hash="hashed", role=UserRole.USER)
    db_session.add(user)
    await db_session.commit()
    await _auth_user(client, settings, user)

    r = await client.get("/api/jobs")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_list_batches_non_admin_returns_403(client, db_session, settings):
    user = User(email="user@example.com", password_hash="hashed", role=UserRole.USER)
    db_session.add(user)
    await db_session.commit()
    await _auth_user(client, settings, user)

    r = await client.get("/api/jobs/batches")
    assert r.status_code == 403


# ===========================================================================
# GET /api/jobs — empty
# ===========================================================================


async def test_list_jobs_empty(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["per_page"] == 50
    assert body["total_pages"] == 0


# ===========================================================================
# GET /api/jobs — filters
# ===========================================================================


async def test_list_jobs_filter_by_repo_id(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo_a = _make_repo("repo-a")
    repo_b = _make_repo("repo-b")
    db_session.add_all([admin, repo_a, repo_b])
    await db_session.commit()

    batch_a = _make_batch(repo_a)
    batch_b = _make_batch(repo_b)
    db_session.add_all([batch_a, batch_b])
    await db_session.commit()

    job_a = _make_job(batch_a, repo_a, step=SyncStep.CLONE)
    job_b = _make_job(batch_b, repo_b, step=SyncStep.PARSE)
    db_session.add_all([job_a, job_b])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get(f"/api/jobs?repo_id={repo_a.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["repository_id"] == str(repo_a.id)


async def test_list_jobs_filter_by_step(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job_clone = _make_job(batch, repo, step=SyncStep.CLONE)
    job_parse = _make_job(batch, repo, step=SyncStep.PARSE)
    db_session.add_all([job_clone, job_parse])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs?step=parse")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["step"] == "parse"


async def test_list_jobs_filter_by_status(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job_q = _make_job(batch, repo, step=SyncStep.CLONE, status=SyncJobStatus.QUEUED)
    job_s = _make_job(batch, repo, step=SyncStep.PARSE, status=SyncJobStatus.SUCCESS)
    job_e = _make_job(batch, repo, step=SyncStep.EMBED, status=SyncJobStatus.ERROR)
    db_session.add_all([job_q, job_s, job_e])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs?status=success")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "success"


async def test_list_jobs_filter_by_skipped_status(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job_success = _make_job(
        batch, repo, step=SyncStep.PARSE, status=SyncJobStatus.SUCCESS
    )
    job_skipped = _make_job(
        batch,
        repo,
        step=SyncStep.GENERATE_SUMMARIES,
        status=SyncJobStatus.SKIPPED,
        error_code="capability_disabled",
        error_msg="Skipped because completion-based generation was disabled for this run.",
    )
    db_session.add_all([job_success, job_skipped])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs?status=skipped")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "skipped"
    assert body["items"][0]["error_code"] == "capability_disabled"


async def test_list_jobs_filter_by_batch_id(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch_x = _make_batch(repo)
    batch_y = _make_batch(repo)
    db_session.add_all([batch_x, batch_y])
    await db_session.commit()

    job_x = _make_job(batch_x, repo, step=SyncStep.CLONE)
    job_y = _make_job(batch_y, repo, step=SyncStep.CLONE)
    db_session.add_all([job_x, job_y])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get(f"/api/jobs?batch_id={batch_x.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["batch_id"] == str(batch_x.id)


async def test_list_jobs_filter_by_search(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job1 = _make_job(batch, repo, step=SyncStep.CLONE, title="Clone repository")
    job2 = _make_job(batch, repo, step=SyncStep.PARSE, title="Parse source files")
    db_session.add_all([job1, job2])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs?search=parse")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert "parse" in body["items"][0]["title"].lower()


async def test_list_jobs_filter_by_repository_name_search(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo_a = _make_repo("fastapi-service")
    repo_b = _make_repo("tailwind-site")
    db_session.add_all([admin, repo_a, repo_b])
    await db_session.commit()

    batch_a = _make_batch(repo_a)
    batch_b = _make_batch(repo_b)
    db_session.add_all([batch_a, batch_b])
    await db_session.commit()

    jobs = [
        _make_job(batch_a, repo_a, step=SyncStep.CLONE, title="Clone repository"),
        _make_job(batch_a, repo_a, step=SyncStep.PARSE, title="Parse source files"),
        _make_job(batch_b, repo_b, step=SyncStep.CLONE, title="Clone repository"),
    ]
    db_session.add_all(jobs)
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs?search=FASTAPI")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {item["repository_id"] for item in body["items"]} == {str(repo_a.id)}


# ===========================================================================
# GET /api/jobs — pagination + sort
# ===========================================================================


async def test_list_jobs_pagination(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    steps = [
        SyncStep.CLONE,
        SyncStep.PARSE,
        SyncStep.EXTRACT_GRAPH,
        SyncStep.EMBED,
        SyncStep.INDEX_REPO_DOCS,
    ]
    for step in steps:
        db_session.add(_make_job(batch, repo, step=step))
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs?per_page=2&page=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["page"] == 2
    assert body["per_page"] == 2
    assert body["total_pages"] == 3


async def test_list_jobs_sort_newest_first(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    early = datetime(2024, 1, 1, tzinfo=timezone.utc)
    late = datetime(2024, 6, 1, tzinfo=timezone.utc)

    job_early = _make_job(batch, repo, step=SyncStep.CLONE, created_at=early)
    job_late = _make_job(batch, repo, step=SyncStep.PARSE, created_at=late)
    db_session.add_all([job_early, job_late])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["id"] == str(job_late.id)
    assert items[1]["id"] == str(job_early.id)


# ===========================================================================
# GET /api/jobs/:id
# ===========================================================================


async def test_get_job_returns_correct_shape(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job = _make_job(
        batch,
        repo,
        step=SyncStep.EMBED,
        status=SyncJobStatus.RUNNING,
        title="Embed 1,247 nodes",
        progress=67,
    )
    job.units_done = 834
    job.units_total = 1247
    job.units_unit = "chunks"
    db_session.add(job)
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get(f"/api/jobs/{job.id}")
    assert r.status_code == 200
    body = r.json()

    # Shape parity with FE_CONTRACT §3 SyncJob example
    assert body["id"] == str(job.id)
    assert body["batch_id"] == str(batch.id)
    assert body["repository_id"] == str(repo.id)
    assert body["bank_id"] is None
    assert body["step"] == "embed"
    assert body["title"] == "Embed 1,247 nodes"
    assert body["status"] == "running"
    assert body["progress"] == 67
    assert body["units"] == {"done": 834, "total": 1247, "unit": "chunks"}
    assert body["error_code"] is None
    assert body["error_msg"] is None
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert "created_at" in body


async def test_get_job_not_found(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.get(f"/api/jobs/{uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_get_job_anonymous_returns_401(client):
    """GET /api/jobs/:id is admin-only — anonymous gets 401."""
    r = await client.get(f"/api/jobs/{uuid4()}")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_get_job_units_null_while_queued(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    # Queued job — no units, no progress
    job = _make_job(batch, repo, step=SyncStep.EMBED, status=SyncJobStatus.QUEUED)
    db_session.add(job)
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get(f"/api/jobs/{job.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["progress"] is None
    assert body["units"] is None


# ===========================================================================
# GET /api/jobs/batches
# ===========================================================================


async def test_list_batches_anonymous_returns_401(client):
    """GET /api/jobs/batches requires admin — anonymous gets 401."""
    r = await client.get("/api/jobs/batches")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_list_batches_empty(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs/batches")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []


async def test_list_batches_returns_summary_shape(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo, label="acme/a")
    db_session.add(batch)
    await db_session.commit()

    job1 = _make_job(batch, repo, step=SyncStep.CLONE, status=SyncJobStatus.SUCCESS)
    job2 = _make_job(batch, repo, step=SyncStep.PARSE, status=SyncJobStatus.QUEUED)
    db_session.add_all([job1, job2])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs/batches")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    b = items[0]
    # Shape parity with FE_CONTRACT §3 SyncBatchSummary
    assert b["batch_id"] == str(batch.id)
    assert b["kind"] == "repo_sync"
    assert b["trigger"] == "manual"
    assert b["label"] == "acme/a"
    assert b["repository_id"] == str(repo.id)
    assert b["bank_id"] is None
    assert "counts" in b
    assert b["counts"]["success"] == 1
    assert b["counts"]["queued"] == 1
    assert b["counts"]["skipped"] == 0
    assert b["is_complete"] is False
    assert "started_at" in b


async def test_list_batches_filter_by_kind(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch_rs = _make_batch(repo, kind=SyncBatchKind.REPO_SYNC)
    batch_bi = SyncBatch(
        kind=SyncBatchKind.BANK_IMPORT,
        trigger=SyncBatchTrigger.MANUAL,
        label="my-bank",
        repository_id=None,
        status=SyncJobStatus.SUCCESS,
    )
    db_session.add_all([batch_rs, batch_bi])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs/batches?kind=bank_import")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "bank_import"


async def test_list_batches_is_complete_true_when_all_terminal_including_skipped(
    client,
    db_session,
    settings,
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    db_session.add(
        _make_job(batch, repo, step=SyncStep.CLONE, status=SyncJobStatus.SUCCESS)
    )
    db_session.add(
        _make_job(
            batch,
            repo,
            step=SyncStep.GENERATE_SUMMARIES,
            status=SyncJobStatus.SKIPPED,
            error_code="capability_disabled",
            error_msg="Skipped because completion-based generation was disabled for this run.",
        )
    )
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs/batches")
    items = r.json()["items"]
    assert items[0]["counts"]["success"] == 1
    assert items[0]["counts"]["skipped"] == 1
    assert items[0]["is_complete"] is True


# ===========================================================================
# GET /api/jobs/batches/:batch_id
# ===========================================================================


async def test_get_batch_not_found(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.get(f"/api/jobs/batches/{uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_get_batch_detail_returns_jobs_asc(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    early = datetime(2024, 1, 1, tzinfo=timezone.utc)
    late = datetime(2024, 6, 1, tzinfo=timezone.utc)
    job_first = _make_job(batch, repo, step=SyncStep.CLONE, created_at=early)
    job_second = _make_job(batch, repo, step=SyncStep.PARSE, created_at=late)
    db_session.add_all([job_first, job_second])
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get(f"/api/jobs/batches/{batch.id}")
    assert r.status_code == 200
    body = r.json()
    assert "batch" in body
    assert "jobs" in body
    assert body["batch"]["batch_id"] == str(batch.id)
    jobs = body["jobs"]
    assert len(jobs) == 2
    # Child jobs sorted created_at ascending (oldest first)
    assert jobs[0]["id"] == str(job_first.id)
    assert jobs[1]["id"] == str(job_second.id)


# ===========================================================================
# GET /api/jobs/stats
# ===========================================================================


async def test_get_stats_anonymous_returns_401(client):
    """GET /api/jobs/stats requires admin — anonymous gets 401."""
    r = await client.get("/api/jobs/stats")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_get_stats_empty_returns_zeroed_window(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs/stats?days=3")
    assert r.status_code == 200
    body = r.json()
    assert body["window_days"] == 3
    assert len(body["runs_by_day"]) == 3
    assert body["total_runs"] == 0
    assert body["success_rate"] == 0
    assert body["median_duration_sec"] is None
    assert body["step_durations"] == []
    # Every day entry has the correct shape
    for day in body["runs_by_day"]:
        assert "date" in day
        assert day["success"] == 0
        assert day["error"] == 0


async def test_get_stats_counts_completed_batches(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    # One completed batch (all success)
    batch = _make_batch(repo, status=SyncJobStatus.SUCCESS)
    db_session.add(batch)
    await db_session.commit()

    for step in [SyncStep.CLONE, SyncStep.PARSE]:
        db_session.add(_make_job(batch, repo, step=step, status=SyncJobStatus.SUCCESS))
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs/stats?days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["total_runs"] == 1
    assert body["success_rate"] == 1.0


async def test_get_stats_window_days_clamped(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    # Query param out of range (ge=1, le=30)
    r = await client.get("/api/jobs/stats?days=0")
    assert r.status_code == 422

    r = await client.get("/api/jobs/stats?days=31")
    assert r.status_code == 422


# ===========================================================================
# POST /api/jobs/:id/retry
# ===========================================================================


async def test_retry_requires_admin(client):
    r = await client.post(f"/api/jobs/{uuid4()}/retry")
    assert r.status_code == 401


async def test_retry_requires_csrf(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    # No X-CSRF-Token header
    r = await client.post(f"/api/jobs/{uuid4()}/retry")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


async def test_retry_not_found(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.post(
        f"/api/jobs/{uuid4()}/retry",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "status",
    [
        SyncJobStatus.QUEUED,
        SyncJobStatus.RUNNING,
        SyncJobStatus.SUCCESS,
        SyncJobStatus.CANCELLED,
    ],
)
async def test_retry_only_allowed_from_error_state(
    client, db_session, settings, status
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job = _make_job(batch, repo, step=SyncStep.EMBED, status=status)
    db_session.add(job)
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.post(
        f"/api/jobs/{job.id}/retry",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


async def test_retry_happy_path(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job = _make_job(
        batch,
        repo,
        step=SyncStep.EMBED,
        status=SyncJobStatus.ERROR,
        progress=42,
        error_code="EMBED_FAILED",
        error_msg="Embedding service unavailable",
    )
    job.units_done = 100
    job.units_total = 200
    job.units_unit = "chunks"
    db_session.add(job)
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.post(
        f"/api/jobs/{job.id}/retry",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["progress"] is None
    assert body["units"] is None
    assert body["error_code"] is None
    assert body["error_msg"] is None
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert body["id"] == str(job.id)


# ===========================================================================
# POST /api/jobs/:id/cancel
# ===========================================================================


async def test_cancel_requires_admin(client):
    r = await client.post(f"/api/jobs/{uuid4()}/cancel")
    assert r.status_code == 401


async def test_cancel_requires_csrf(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.post(f"/api/jobs/{uuid4()}/cancel")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


async def test_cancel_not_found(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _auth_admin(client, settings, admin)

    r = await client.post(
        f"/api/jobs/{uuid4()}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "status",
    [
        SyncJobStatus.SUCCESS,
        SyncJobStatus.ERROR,
        SyncJobStatus.CANCELLED,
    ],
)
async def test_cancel_only_allowed_from_queued_or_running(
    client, db_session, settings, status
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job = _make_job(batch, repo, step=SyncStep.CLONE, status=status)
    db_session.add(job)
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.post(
        f"/api/jobs/{job.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


@pytest.mark.parametrize("status", [SyncJobStatus.QUEUED, SyncJobStatus.RUNNING])
async def test_cancel_happy_path(client, db_session, settings, status):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job = _make_job(batch, repo, step=SyncStep.PARSE, status=status)
    db_session.add(job)
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.post(
        f"/api/jobs/{job.id}/cancel",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["finished_at"] is not None
    assert body["error_msg"] == "Cancelled by user."


# ===========================================================================
# FE_CONTRACT shape parity — SyncJob running example
# ===========================================================================


async def test_shape_parity_sync_job_running(client, db_session, settings):
    """Exact field match against FE_CONTRACT §3 example SyncJob (running)."""
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo)
    db_session.add(batch)
    await db_session.commit()

    job = _make_job(
        batch,
        repo,
        step=SyncStep.EMBED,
        status=SyncJobStatus.RUNNING,
        title="Embed 1,247 nodes",
        progress=67,
    )
    job.units_done = 834
    job.units_total = 1247
    job.units_unit = "chunks"
    db_session.add(job)
    await db_session.commit()

    await _auth_admin(client, settings, admin)
    r = await client.get(f"/api/jobs/{job.id}")
    assert r.status_code == 200
    body = r.json()

    required_fields = {
        "id",
        "batch_id",
        "repository_id",
        "bank_id",
        "step",
        "title",
        "status",
        "progress",
        "units",
        "error_code",
        "error_msg",
        "started_at",
        "finished_at",
        "created_at",
    }
    assert required_fields.issubset(body.keys()), (
        f"Missing fields: {required_fields - body.keys()}"
    )

    assert body["step"] == "embed"
    assert body["status"] == "running"
    assert body["progress"] == 67
    assert body["units"] == {"done": 834, "total": 1247, "unit": "chunks"}
    assert body["error_code"] is None
    assert body["error_msg"] is None


# ===========================================================================
# FE_CONTRACT shape parity — SyncBatchSummary example
# ===========================================================================


async def test_shape_parity_sync_batch_summary(client, db_session, settings):
    """Exact field match against FE_CONTRACT §3 example SyncBatchSummary."""
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repo = _make_repo()
    db_session.add_all([admin, repo])
    await db_session.commit()

    batch = _make_batch(repo, trigger=SyncBatchTrigger.INITIAL, label="fastapi/fastapi")
    db_session.add(batch)
    await db_session.commit()

    for step, status in [
        (SyncStep.CLONE, SyncJobStatus.SUCCESS),
        (SyncStep.PARSE, SyncJobStatus.SUCCESS),
        (SyncStep.EXTRACT_GRAPH, SyncJobStatus.SUCCESS),
        (SyncStep.EMBED, SyncJobStatus.RUNNING),
        (SyncStep.INDEX_REPO_DOCS, SyncJobStatus.QUEUED),
    ]:
        db_session.add(_make_job(batch, repo, step=step, status=status))
    await db_session.commit()

    await _auth_admin(client, settings, admin)

    r = await client.get("/api/jobs/batches")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    b = items[0]

    required_fields = {
        "batch_id",
        "kind",
        "trigger",
        "label",
        "repository_id",
        "bank_id",
        "counts",
        "started_at",
        "is_complete",
    }
    assert required_fields.issubset(b.keys()), f"Missing: {required_fields - b.keys()}"

    count_fields = {
        "queued",
        "running",
        "paused",
        "skipped",
        "success",
        "error",
        "cancelled",
    }
    assert count_fields.issubset(b["counts"].keys())

    assert b["kind"] == "repo_sync"
    assert b["trigger"] == "initial"
    assert b["label"] == "fastapi/fastapi"
    assert b["counts"]["success"] == 3
    assert b["counts"]["running"] == 1
    assert b["counts"]["queued"] == 1
    assert b["counts"]["skipped"] == 0
    assert b["is_complete"] is False
