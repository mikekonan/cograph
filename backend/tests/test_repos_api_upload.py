"""Endpoint tests for the zip-archive ingest path:

  - `POST /repos/upload` — initial upload + repo creation
  - `POST /repos/{host}/{owner}/{name}/upload` — re-snapshot an existing zip-source repo
  - guards: reindex / webhook / non-manual schedule are rejected for
    zip-source repos
  - delete cleans up the persisted archive on disk

Pattern mirrors `test_repos_api.py` — test client + fake orchestrator
override.
"""

from __future__ import annotations

import io
import zipfile
from uuid import uuid4


from backend.app.core.auth import TokenType, create_token
from backend.app.core.deps import get_repo_sync_orchestrator
from backend.app.models.enums import (
    RepoSource,
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    SyncSchedule,
    UserRole,
)
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.pipeline.orchestrator import RepoSyncEnqueueResult


_TEST_CSRF = "csrf-token"


# ----- shared fakes / helpers ----------------------------------------


class _FakeOrchestrator:
    def __init__(self, result: RepoSyncEnqueueResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def enqueue_repository_sync(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


async def _authenticate_admin(client, settings, admin: User) -> None:
    token = create_token(
        user_id=admin.id,
        role=admin.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


def _zip_payload(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


async def _make_admin(db_session) -> User:
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    db_session.add(admin)
    await db_session.commit()
    return admin


# ----- POST /repos/upload --------------------------------------------


async def test_upload_repository_archive_requires_admin(client):
    response = await client.post(
        "/api/repos/upload",
        files={"archive": ("repo.zip", b"PK\x03\x04", "application/zip")},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_upload_repository_archive_requires_csrf(client, db_session, settings):
    admin = await _make_admin(db_session)
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        "/api/repos/upload",
        files={"archive": ("repo.zip", b"PK\x03\x04", "application/zip")},
    )
    assert response.status_code == 403


async def test_upload_repository_archive_rejects_non_zip_filename(
    client, db_session, settings
):
    admin = await _make_admin(db_session)
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        "/api/repos/upload",
        files={"archive": ("not-a-zip.tar", b"x", "application/octet-stream")},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_upload_repository_archive_rejects_unsupported_content_type(
    client, db_session, settings
):
    admin = await _make_admin(db_session)
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        "/api/repos/upload",
        files={"archive": ("repo.zip", b"x", "text/plain")},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_upload_repository_archive_rejects_invalid_zip_bytes(
    client, db_session, settings
):
    admin = await _make_admin(db_session)
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        "/api/repos/upload",
        files={"archive": ("repo.zip", b"not really a zip", "application/zip")},
        data={"host": "local.zip", "owner": "demo", "name": "bogus"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ARCHIVE_INVALID"


async def test_upload_repository_archive_happy_path(client, app, db_session, settings):
    admin = await _make_admin(db_session)
    await _authenticate_admin(client, settings, admin)

    fake_result = RepoSyncEnqueueResult(
        repository_id=uuid4(),
        sync_run_id=uuid4(),
        batch_id=None,
        status=RepoSyncRunStatus.QUEUED,
        requested_ref=None,
        deduplicated=False,
    )
    orchestrator = _FakeOrchestrator(result=fake_result)
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    payload = _zip_payload({"src/main.py": "print('hi')\n", "README.md": "# x\n"})

    response = await client.post(
        "/api/repos/upload",
        files={"archive": ("my-project.zip", payload, "application/zip")},
        data={"host": "local.zip", "owner": "demo", "name": "my-project"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 202, response.text
    data = response.json()
    assert data["source"] == "zip"
    assert data["branch"] == "upload"
    assert data["host"] == "local.zip"
    assert data["owner"] == "demo"
    assert data["name"] == "my-project"
    assert data["sync_schedule"] == "manual"
    assert data["git_url"] == "zip://local.zip/demo/my-project"
    # last_commit must be None on initial upload — pre-populating it would
    # collide with the orchestrator dedup check (which compares against the
    # zip's sha256 returned by prepare_checkout) and skip the sync run.
    assert data["last_commit"] is None

    from uuid import UUID

    repo_id = UUID(data["id"])
    saved = await db_session.get(Repository, repo_id)
    assert saved is not None
    assert saved.source is RepoSource.ZIP

    # Sync was enqueued with INITIAL trigger.
    assert len(orchestrator.calls) == 1
    assert orchestrator.calls[0]["trigger_kind"] is RepoSyncTriggerKind.INITIAL


# ----- POST /repos/{host}/{owner}/{name}/upload ----------------------


async def test_replace_repository_archive_rejects_git_source(
    client, db_session, settings
):
    admin = await _make_admin(db_session)
    repo = Repository(
        host="example.com",
        git_url="https://example.com/x/y.git",
        source=RepoSource.GIT,
        name="y",
        owner="x",
        branch="main",
    )
    db_session.add(repo)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    payload = _zip_payload({"a.txt": "x"})
    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/upload",
        files={"archive": ("repo.zip", payload, "application/zip")},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOT_ZIP_SOURCED"


async def test_replace_repository_archive_replaces_for_zip_source(
    client, app, db_session, settings
):
    admin = await _make_admin(db_session)
    repo = Repository(
        host="example.com",
        git_url=f"zip://{uuid4()}",
        source=RepoSource.ZIP,
        name="archive",
        owner=str(admin.id),
        branch="upload",
        sync_schedule=SyncSchedule.MANUAL,
        last_commit="oldcommit",
    )
    db_session.add(repo)
    await db_session.commit()

    fake_result = RepoSyncEnqueueResult(
        repository_id=repo.id,
        sync_run_id=uuid4(),
        batch_id=None,
        status=RepoSyncRunStatus.QUEUED,
        requested_ref=None,
        deduplicated=False,
    )
    orchestrator = _FakeOrchestrator(result=fake_result)
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator
    await _authenticate_admin(client, settings, admin)

    payload = _zip_payload({"new.py": "print('new')\n"})
    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/upload",
        files={"archive": ("new.zip", payload, "application/zip")},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"

    await db_session.refresh(repo)
    # Endpoint does NOT touch last_commit — the worker is the single writer
    # of that field after a successful sync. The previous value is preserved
    # so a failed re-sync still shows the last good commit.
    assert repo.last_commit == "oldcommit"
    assert repo.status is RepositoryStatus.PENDING


# ----- guards: reindex / webhook / schedule --------------------------


async def test_reindex_rejected_for_zip_source(client, db_session, settings):
    admin = await _make_admin(db_session)
    repo = Repository(
        host="example.com",
        git_url=f"zip://{uuid4()}",
        source=RepoSource.ZIP,
        name="archive",
        owner=str(admin.id),
        branch="upload",
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/reindex",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REINDEX_NOT_SUPPORTED_FOR_ZIP"


async def test_webhook_rejected_for_zip_source(client, db_session, settings):
    admin = await _make_admin(db_session)
    repo = Repository(
        host="example.com",
        git_url=f"zip://{uuid4()}",
        source=RepoSource.ZIP,
        name="archive",
        owner=str(admin.id),
        branch="upload",
        sync_schedule=SyncSchedule.MANUAL,
        webhook_secret="secret",
    )
    db_session.add(repo)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/webhook",
        headers={"X-Cograph-Webhook-Secret": "secret"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WEBHOOK_DISABLED"


async def test_schedule_rejected_for_zip_source(client, db_session, settings):
    admin = await _make_admin(db_session)
    repo = Repository(
        host="example.com",
        git_url=f"zip://{uuid4()}",
        source=RepoSource.ZIP,
        name="archive",
        owner=str(admin.id),
        branch="upload",
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.patch(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}",
        json={"sync_schedule": "daily"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SCHEDULE_NOT_SUPPORTED_FOR_ZIP"


# ----- delete cleanup ------------------------------------------------


async def test_delete_zip_repository_cleans_archive_and_extracted(
    client, app, db_session, settings, tmp_path
):
    admin = await _make_admin(db_session)
    fake_result = RepoSyncEnqueueResult(
        repository_id=uuid4(),
        sync_run_id=uuid4(),
        batch_id=None,
        status=RepoSyncRunStatus.QUEUED,
        requested_ref=None,
        deduplicated=False,
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: _FakeOrchestrator(
        result=fake_result
    )
    await _authenticate_admin(client, settings, admin)

    payload = _zip_payload({"a.txt": "hello"})
    create_response = await client.post(
        "/api/repos/upload",
        files={"archive": ("demo.zip", payload, "application/zip")},
        data={"host": "local.zip", "owner": "demo", "name": "demo"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert create_response.status_code == 202
    repo_id = create_response.json()["id"]
    repo_host = create_response.json()["host"]
    repo_owner = create_response.json()["owner"]
    repo_name = create_response.json()["name"]

    archive_path = settings.git.checkouts_root / f"{repo_id}.zip"
    assert archive_path.exists(), "upload should have persisted the archive on disk"

    delete_response = await client.delete(
        f"/api/repos/{repo_host}/{repo_owner}/{repo_name}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()
    assert delete_response.status_code == 204
    assert not archive_path.exists(), "delete should remove the archive"
