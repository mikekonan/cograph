from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from backend.app.core.auth import TokenType, create_token
from backend.app.core.deps import get_repo_sync_orchestrator
from backend.app.models.enums import (
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repo_document import RepoDocument
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile
from backend.app.models.user import User
from backend.app.pipeline.checkout import GitCheckoutError
from backend.app.pipeline.orchestrator import JobEnqueueError, RepoSyncEnqueueResult


class _FakeOrchestrator:
    def __init__(
        self,
        result: RepoSyncEnqueueResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def enqueue_repository_sync(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class _FastReadyOrchestrator(_FakeOrchestrator):
    async def enqueue_repository_sync(self, **kwargs):
        self.calls.append(kwargs)
        session = kwargs["session"]
        repository = await session.get(Repository, kwargs["repository_id"])
        assert repository is not None
        repository.status = RepositoryStatus.READY
        await session.commit()
        assert self.result is not None
        return self.result


_TEST_CSRF = "csrf-token"


def _hash_pat(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _authenticate_admin(client, settings, admin: User) -> None:
    token = create_token(
        user_id=admin.id,
        role=admin.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    # Also set the csrf cookie so the double-submit validation passes.
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


async def _mint_pat(
    db_session,
    user: User,
    plaintext: str,
    *,
    scopes: list[str] | None = None,
) -> dict[str, str]:
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="test-token",
            token_hash=_hash_pat(plaintext),
            token_prefix=plaintext[:16],
            scopes=scopes or ["api:read"],
        )
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {plaintext}"}


async def test_reindex_repository_requires_admin(client):
    response = await client.post("/api/repos/example.com/acme/demo/reindex")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_reindex_repository_enqueues_sync_run(client, app, db_session, settings):
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add_all([admin, repository])
    await db_session.commit()

    await _authenticate_admin(client, settings, admin)

    fake_result = RepoSyncEnqueueResult(
        repository_id=repository.id,
        sync_run_id=uuid4(),
        batch_id=None,
        status=RepoSyncRunStatus.QUEUED,
        requested_ref="abc123",
        deduplicated=False,
    )
    orchestrator = _FakeOrchestrator(result=fake_result)
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/reindex",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 202
    assert response.json() == {
        "id": str(fake_result.sync_run_id),
        "status": "pending",
    }
    assert len(orchestrator.calls) == 1
    assert orchestrator.calls[0]["repository_id"] == repository.id
    assert orchestrator.calls[0]["trigger_kind"] is RepoSyncTriggerKind.MANUAL
    assert orchestrator.calls[0]["requested_by"] == admin.id


async def test_reindex_repository_maps_clone_failures(
    client, app, db_session, settings
):
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add_all([admin, repository])
    await db_session.commit()

    await _authenticate_admin(client, settings, admin)
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: _FakeOrchestrator(
        error=GitCheckoutError("Failed to clone repository")
    )

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/reindex",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GIT_CLONE_FAILED"


async def test_reindex_repository_maps_queue_setup_failures(
    client,
    app,
    db_session,
    settings,
    monkeypatch,
):
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add_all([admin, repository])
    await db_session.commit()

    await _authenticate_admin(client, settings, admin)

    async def _raise_connection_error(*args, **kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("backend.app.core.deps.create_pool", _raise_connection_error)

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/reindex",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    app.dependency_overrides.clear()


async def test_reindex_repository_not_found_wins_before_queue_setup_failure(
    client,
    db_session,
    settings,
    monkeypatch,
):
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    db_session.add(admin)
    await db_session.commit()

    await _authenticate_admin(client, settings, admin)

    async def _raise_connection_error(*args, **kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("backend.app.core.deps.create_pool", _raise_connection_error)

    response = await client.post(
        "/api/repos/example.com/missing/repo/reindex",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_update_repository_schedule_updates_sync_settings(
    client, db_session, settings
):
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add_all([admin, repository])
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.patch(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}",
        json={"sync_schedule": "webhook"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    refreshed_repository = await db_session.get(Repository, repository.id)
    assert refreshed_repository is not None
    await db_session.refresh(refreshed_repository)

    assert response.status_code == 200
    assert response.json()["sync_schedule"] == "webhook"
    assert response.json()["next_sync_at"] is None
    assert response.json()["stats"]["documents_count"] == 0
    assert refreshed_repository.sync_schedule is SyncSchedule.WEBHOOK
    assert refreshed_repository.next_sync_at is None
    assert refreshed_repository.webhook_secret is not None


async def test_update_repository_visibility(client, db_session, settings):
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add_all([admin, repository])
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.patch(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}",
        json={"visibility": "admin_only"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    refreshed_repository = await db_session.get(Repository, repository.id)
    assert refreshed_repository is not None
    await db_session.refresh(refreshed_repository)

    assert response.status_code == 200
    assert response.json()["visibility"] == "admin_only"
    assert refreshed_repository.visibility is RepositoryVisibility.ADMIN_ONLY


async def test_get_repository_webhook_config_requires_admin(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.WEBHOOK,
        webhook_secret="secret-token",
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(
        f"/api/admin/repos/{repository.host}/{repository.owner}/{repository.name}/webhook"
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_get_repository_webhook_config_returns_secret_for_admin(
    client,
    db_session,
    settings,
):
    admin = User(
        email="admin@example.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.WEBHOOK,
        webhook_secret="secret-token",
    )
    db_session.add_all([admin, repository])
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.get(
        f"/api/admin/repos/{repository.host}/{repository.owner}/{repository.name}/webhook"
    )

    assert response.status_code == 200
    assert response.json() == {
        "repository_id": str(repository.id),
        "sync_schedule": "webhook",
        "webhook_secret": "secret-token",
        "webhook_path": f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/webhook",
    }


async def test_trigger_repository_webhook_rejects_invalid_secret(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.WEBHOOK,
        webhook_secret="expected-secret",
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/webhook",
        headers={"X-Cograph-Webhook-Secret": "wrong-secret"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_trigger_repository_webhook_enqueues_sync_run(client, app, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.WEBHOOK,
        webhook_secret="expected-secret",
    )
    db_session.add(repository)
    await db_session.commit()

    fake_result = RepoSyncEnqueueResult(
        repository_id=repository.id,
        sync_run_id=uuid4(),
        batch_id=None,
        status=RepoSyncRunStatus.QUEUED,
        requested_ref="abc123",
        deduplicated=False,
    )
    orchestrator = _FakeOrchestrator(result=fake_result)
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/webhook",
        headers={"X-Cograph-Webhook-Secret": "expected-secret"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "id": str(fake_result.sync_run_id),
        "status": "pending",
    }
    assert len(orchestrator.calls) == 1
    assert orchestrator.calls[0]["repository_id"] == repository.id
    assert orchestrator.calls[0]["trigger_kind"] is RepoSyncTriggerKind.WEBHOOK


async def test_trigger_repository_webhook_returns_skipped_status(
    client, app, db_session
):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        sync_schedule=SyncSchedule.WEBHOOK,
        webhook_secret="expected-secret",
    )
    db_session.add(repository)
    await db_session.commit()

    fake_result = RepoSyncEnqueueResult(
        repository_id=repository.id,
        sync_run_id=uuid4(),
        batch_id=None,
        status=RepoSyncRunStatus.SKIPPED,
        requested_ref="abc123",
        deduplicated=False,
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: _FakeOrchestrator(
        result=fake_result
    )

    response = await client.post(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/webhook",
        headers={"X-Cograph-Webhook-Secret": "expected-secret"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "id": str(fake_result.sync_run_id),
        "status": "skipped",
    }


# ---------------------------------------------------------------------------
# GET /api/repos — list
# ---------------------------------------------------------------------------


async def test_list_repositories_anonymous(client, db_session):
    repos = [
        Repository(
            host="github.com",
            git_url=f"https://github.com/acme/repo{i}.git",
            name=f"repo{i}",
            owner="acme",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        )
        for i in range(3)
    ]
    db_session.add_all(repos)
    await db_session.commit()

    response = await client.get("/api/repos")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert data["page"] == 1
    assert data["per_page"] == 20
    assert data["total_pages"] == 1


async def test_list_repositories_includes_next_sync_at(client, db_session):
    next_sync_at = datetime(2026, 4, 23, 2, 0, 0, tzinfo=UTC)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/scheduled.git",
        name="scheduled",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        sync_schedule=SyncSchedule.DAILY,
        next_sync_at=next_sync_at,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get("/api/repos")

    assert response.status_code == 200
    assert response.json()["items"][0][
        "next_sync_at"
    ] == next_sync_at.isoformat().replace("+00:00", "Z")


async def test_list_repositories_anonymous_excludes_admin_only(client, db_session):
    db_session.add_all(
        [
            Repository(
                host="example.com",
                git_url="https://github.com/acme/public.git",
                name="public",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.PUBLIC,
            ),
            Repository(
                host="example.com",
                git_url="https://github.com/acme/secret.git",
                name="secret",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.ADMIN_ONLY,
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/repos")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert [item["name"] for item in data["items"]] == ["public"]


async def test_list_repositories_admin_sees_admin_only(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add_all(
        [
            admin,
            Repository(
                host="example.com",
                git_url="https://github.com/acme/public.git",
                name="public",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.PUBLIC,
            ),
            Repository(
                host="example.com",
                git_url="https://github.com/acme/secret.git",
                name="secret",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.ADMIN_ONLY,
            ),
        ]
    )
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.get("/api/repos")

    assert response.status_code == 200
    assert response.json()["total"] == 2


async def test_list_repositories_owner_sees_admin_only(client, db_session, settings):
    owner = User(email="owner@example.com", password_hash="hashed", role=UserRole.OWNER)
    db_session.add_all(
        [
            owner,
            Repository(
                host="example.com",
                git_url="https://github.com/acme/public.git",
                name="public",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.PUBLIC,
            ),
            Repository(
                host="example.com",
                git_url="https://github.com/acme/secret.git",
                name="secret",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.ADMIN_ONLY,
            ),
        ]
    )
    await db_session.commit()
    await _authenticate_admin(client, settings, owner)

    response = await client.get("/api/repos")

    assert response.status_code == 200
    assert response.json()["total"] == 2


async def test_list_repositories_pat_user_without_grant_sees_only_public(
    client, db_session
):
    """USER-tier PAT without any group grant must NOT see ADMIN_ONLY
    repos. This is the new ACL contract: visibility funnel + group
    grants, no implicit access just because you authenticated.
    """
    member = User(email="member@example.com", password_hash="hashed", role=UserRole.USER)
    db_session.add_all(
        [
            member,
            Repository(
                host="example.com",
                git_url="https://github.com/acme/public.git",
                name="public",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.PUBLIC,
            ),
            Repository(
                host="example.com",
                git_url="https://github.com/acme/secret.git",
                name="secret",
                owner="acme",
                branch="main",
                visibility=RepositoryVisibility.ADMIN_ONLY,
            ),
        ]
    )
    await db_session.commit()
    headers = await _mint_pat(
        db_session,
        member,
        "cgr_pat_repo_read_member_token_000000000000000000000000",
    )

    response = await client.get("/api/repos", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert {item["name"] for item in body["items"]} == {"public"}


async def test_list_repositories_pat_user_with_grant_sees_admin_only(
    client, db_session
):
    """USER-tier PAT in a group with a READ grant on an ADMIN_ONLY
    repo MUST see that repo through the funnel. Proves the grant
    extension reaches REST list endpoints.
    """
    from backend.app.models.enums import GrantLevel
    from backend.app.models.group import Group, GroupMember, RepositoryGrant

    member = User(email="grantee@example.com", password_hash="hashed", role=UserRole.USER)
    public_repo = Repository(
        host="example.com",
        git_url="https://github.com/acme/public.git",
        name="public",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    secret_repo = Repository(
        host="example.com",
        git_url="https://github.com/acme/secret.git",
        name="secret",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add_all([member, public_repo, secret_repo])
    await db_session.commit()

    group = Group(name="grantees")
    db_session.add(group)
    await db_session.commit()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member.id),
            RepositoryGrant(
                group_id=group.id,
                repository_id=secret_repo.id,
                level=GrantLevel.READ.value,
            ),
        ]
    )
    await db_session.commit()

    headers = await _mint_pat(
        db_session,
        member,
        "cgr_pat_repo_read_grantee_token_0000000000000000000000",
    )

    response = await client.get("/api/repos", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"public", "secret"}


async def test_list_repositories_pagination(client, db_session):
    repos = [
        Repository(
            host="github.com",
            git_url=f"https://github.com/acme/repo{i}.git",
            name=f"repo{i}",
            owner="acme",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        )
        for i in range(5)
    ]
    db_session.add_all(repos)
    await db_session.commit()

    response = await client.get("/api/repos?per_page=2&page=2")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 2
    assert data["per_page"] == 2
    assert data["total_pages"] == 3


# ---------------------------------------------------------------------------
# GET /api/repos/{repository_id} — single
# ---------------------------------------------------------------------------


async def test_get_repository_anonymous(client, db_session):
    next_sync_at = datetime(2026, 4, 23, 2, 0, 0, tzinfo=UTC)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        sync_schedule=SyncSchedule.DAILY,
        next_sync_at=next_sync_at,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(repository.id)
    assert data["name"] == "demo"
    assert data["owner"] == "acme"
    assert "stats" in data
    assert data["visibility"] == "public"
    assert data["next_sync_at"] == next_sync_at.isoformat().replace("+00:00", "Z")


async def test_get_repository_anonymous_hides_admin_only_repo(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/secret.git",
        name="secret",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_repository_admin_can_read_admin_only_repo(
    client, db_session, settings
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/secret.git",
        name="secret",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add_all([admin, repository])
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    assert response.json()["visibility"] == "admin_only"


async def test_get_repository_owner_can_read_admin_only_repo(
    client, db_session, settings
):
    owner = User(email="owner@example.com", password_hash="hashed", role=UserRole.OWNER)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/secret.git",
        name="secret",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add_all([owner, repository])
    await db_session.commit()
    await _authenticate_admin(client, settings, owner)

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    assert response.json()["visibility"] == "admin_only"


async def test_get_repository_pat_user_without_grant_404s_on_admin_only(
    client, db_session
):
    """USER-tier PAT without a grant must get a 404 (not 403) on an
    ADMIN_ONLY repo — funnel hides its existence to avoid leaking.
    """
    member = User(email="member-read@example.com", password_hash="hashed", role=UserRole.USER)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/secret.git",
        name="secret",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add_all([member, repository])
    await db_session.commit()
    headers = await _mint_pat(
        db_session,
        member,
        "cgr_pat_repo_detail_member_token_0000000000000000000000",
    )

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}",
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_repository_pat_user_with_grant_can_read_admin_only(
    client, db_session
):
    """USER-tier PAT with a READ grant on an ADMIN_ONLY repo MUST be
    able to GET it by slug — the positive counterpart of the 404 case.
    """
    from backend.app.models.enums import GrantLevel
    from backend.app.models.group import Group, GroupMember, RepositoryGrant

    member = User(email="grantee-detail@example.com", password_hash="hashed", role=UserRole.USER)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/secret.git",
        name="secret",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )
    db_session.add_all([member, repository])
    await db_session.commit()

    group = Group(name="detail-grantees")
    db_session.add(group)
    await db_session.commit()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member.id),
            RepositoryGrant(
                group_id=group.id,
                repository_id=repository.id,
                level=GrantLevel.READ.value,
            ),
        ]
    )
    await db_session.commit()

    headers = await _mint_pat(
        db_session,
        member,
        "cgr_pat_repo_detail_grantee_token_00000000000000000000",
    )

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["visibility"] == "admin_only"


async def test_get_repository_not_found(client):
    response = await client.get("/api/repos/example.com/missing/repo")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /api/repos — create
# ---------------------------------------------------------------------------


async def test_create_repository_requires_admin(client):
    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/demo.git"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_create_repository_requires_csrf(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    # POST without X-CSRF-Token header
    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/demo.git"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_create_repository_happy_path(client, app, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
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

    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/demo.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 202
    data = response.json()
    assert data["name"] == "demo"
    assert data["owner"] == "acme"
    assert data["branch"] == "main"
    assert data["git_url"] == "https://github.com/acme/demo.git"
    assert data["status"] == "pending"
    assert data["visibility"] == "admin_only"

    # Verify repo row was persisted
    from uuid import UUID as _UUID

    repo_id = _UUID(data["id"])
    saved = await db_session.get(Repository, repo_id)
    assert saved is not None
    assert saved.name == "demo"
    assert saved.visibility is RepositoryVisibility.ADMIN_ONLY

    # Verify sync was enqueued with INITIAL trigger (first-ever sync for this repo)
    assert len(orchestrator.calls) == 1
    assert orchestrator.calls[0]["trigger_kind"] is RepoSyncTriggerKind.INITIAL
    assert orchestrator.calls[0]["requested_by"] == admin.id


async def test_create_repository_response_stays_pending_when_sync_finishes_fast(
    client,
    app,
    db_session,
    settings,
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    orchestrator = _FastReadyOrchestrator(
        result=RepoSyncEnqueueResult(
            repository_id=uuid4(),
            sync_run_id=uuid4(),
            batch_id=None,
            status=RepoSyncRunStatus.SUCCESS,
            requested_ref="abc1234",
            deduplicated=False,
        )
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/fast.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["last_commit"] is None
    assert data["stats"]["modules_count"] == 0


async def test_create_repository_accepts_public_visibility(
    client, app, db_session, settings
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    orchestrator = _FakeOrchestrator(
        result=RepoSyncEnqueueResult(
            repository_id=uuid4(),
            sync_run_id=uuid4(),
            batch_id=None,
            status=RepoSyncRunStatus.QUEUED,
            requested_ref=None,
            deduplicated=False,
        )
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    response = await client.post(
        "/api/repos",
        json={
            "git_url": "https://github.com/acme/public.git",
            "visibility": "public",
        },
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 202
    data = response.json()
    assert data["visibility"] == "public"
    from uuid import UUID as _UUID

    saved = await db_session.get(Repository, _UUID(data["id"]))
    assert saved is not None
    assert saved.visibility is RepositoryVisibility.PUBLIC


async def test_create_repository_rejects_scp_ssh_url(client, db_session, settings):
    """SCP-style SSH URLs (git@host:owner/repo.git) are rejected.

    Cograph clones via HTTPS + GIT_ASKPASS-injected PATs. The backend
    image ships ``git`` but not ``openssh-client``, and Phase 30.5
    git_credentials are HTTPS PATs by design — letting an SSH URL
    through here results in ``cannot run ssh: No such file or directory``
    much later, leaving a corpse repo row in the ERROR state.
    """
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        "/api/repos",
        json={"git_url": "git@github.com:acme/demo.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "GIT_URL_SSH_UNSUPPORTED"
    assert "https://github.com/acme/demo.git" in body["error"]["message"]


async def test_create_repository_rejects_ssh_url_scheme(client, db_session, settings):
    """``ssh://`` URLs are rejected with the same friendly hint."""
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        "/api/repos",
        json={"git_url": "ssh://git@github.com/acme/demo.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "GIT_URL_SSH_UNSUPPORTED"
    assert "https://github.com/acme/demo.git" in body["error"]["message"]


async def test_create_repository_rejects_http_url(client, db_session, settings):
    """Cleartext http:// URLs must be rejected — MITM can inject malicious code."""
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.post(
        "/api/repos",
        json={"git_url": "http://github.com/acme/demo.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_create_repository_marks_error_on_git_failure(
    client, app, db_session, settings
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: _FakeOrchestrator(
        error=GitCheckoutError("could not clone")
    )

    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/error-git.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GIT_CLONE_FAILED"

    # The Repository row must be discoverable and in ERROR state.
    from sqlalchemy import select as _select

    repo = await db_session.scalar(
        _select(Repository).where(
            Repository.git_url == "https://github.com/acme/error-git.git"
        )
    )
    assert repo is not None
    assert repo.status == RepositoryStatus.ERROR
    assert repo.error_msg  # non-empty


async def test_create_repository_marks_error_on_queue_failure(
    client, app, db_session, settings
):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: _FakeOrchestrator(
        error=JobEnqueueError("redis not available")
    )

    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/error-queue.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    from sqlalchemy import select as _select

    repo = await db_session.scalar(
        _select(Repository).where(
            Repository.git_url == "https://github.com/acme/error-queue.git"
        )
    )
    assert repo is not None
    assert repo.status == RepositoryStatus.ERROR
    assert repo.error_msg  # non-empty


async def test_create_repository_duplicate(client, app, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    existing = Repository(
        host="github.com",
        git_url="https://github.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add_all([admin, existing])
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    orchestrator = _FakeOrchestrator(
        result=RepoSyncEnqueueResult(
            repository_id=uuid4(),
            sync_run_id=uuid4(),
            batch_id=None,
            status=RepoSyncRunStatus.QUEUED,
            requested_ref=None,
            deduplicated=False,
        )
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/demo.git", "branch": "main"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REPOSITORY_EXISTS"


async def test_create_repository_uses_initial_trigger_on_first_sync(
    client, app, db_session, settings
):
    """First-ever sync for a repo uses INITIAL trigger; JobsPage shows the correct icon."""

    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    orchestrator = _FakeOrchestrator(
        result=RepoSyncEnqueueResult(
            repository_id=uuid4(),
            sync_run_id=uuid4(),
            batch_id=None,
            status=RepoSyncRunStatus.QUEUED,
            requested_ref=None,
            deduplicated=False,
        )
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/first-sync.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 202
    assert len(orchestrator.calls) == 1
    # First sync must use INITIAL so JobsPage renders the "initial index" icon
    assert orchestrator.calls[0]["trigger_kind"] is RepoSyncTriggerKind.INITIAL


# ---------------------------------------------------------------------------
# DELETE /api/repos/{repository_id}
# ---------------------------------------------------------------------------


async def test_delete_repository_requires_admin_and_csrf(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add(repository)
    await db_session.commit()

    # No auth at all → 401
    response = await client.delete(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_delete_repository_not_found(client, db_session, settings):
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    response = await client.delete(
        "/api/repos/example.com/missing/repo",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_delete_repository_soft_deletes_and_enqueues_purge(
    client, db_session, settings, monkeypatch
):
    """DELETE flips the row to DELETING + stamps deleted_at + enqueues a
    `purge_repository` job, then returns 204. The actual cascade drain
    runs in the background worker (covered by test_purge_worker.py) —
    here we only verify the synchronous half is fast and side-effecting
    only what the user sees.
    """
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add_all([admin, repository])
    await db_session.commit()

    # Attach a child row to prove the synchronous handler does NOT
    # cascade-delete it any more (the worker will).
    source_file = SourceFile(
        repository_id=repository.id,
        file_path="app/main.py",
        language="python",
        kind="code",
        raw_bytes=b"print('hello')",
        content_hash="abc123",
        bytes=14,
    )
    db_session.add(source_file)
    await db_session.commit()

    await _authenticate_admin(client, settings, admin)

    enqueued: list[tuple[str, tuple[Any, ...]]] = []

    class _FakePool:
        async def enqueue_job(self, name: str, *args: Any) -> None:
            enqueued.append((name, args))

        async def aclose(self) -> None:
            return None

    async def _fake_create_pool(*args: Any, **kwargs: Any) -> _FakePool:
        del args, kwargs
        return _FakePool()

    monkeypatch.setattr("backend.app.api.repos.create_pool", _fake_create_pool)

    response = await client.delete(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 204

    # Row still exists, but soft-deleted (status=DELETING, deleted_at set).
    # The HTTP handler committed in a separate session, so query the DB
    # directly (don't rely on the identity map cache from the test session).
    row = (
        await db_session.execute(
            select(Repository.status, Repository.deleted_at).where(
                Repository.id == repository.id
            )
        )
    ).one()
    assert row.status is RepositoryStatus.DELETING
    assert row.deleted_at is not None

    # Child row is still in the DB — the worker is responsible for
    # tearing it down. The synchronous path must not block on this.
    sf_count = await db_session.scalar(
        select(func.count(SourceFile.id)).where(SourceFile.id == source_file.id)
    )
    assert sf_count == 1, "child rows should not be deleted by the synchronous handler"

    # Exactly one purge_repository job enqueued with the repo's UUID.
    assert enqueued == [("purge_repository", (str(repository.id),))]


async def test_delete_repository_hides_row_from_get_and_list(
    client, db_session, settings, monkeypatch
):
    """A soft-deleted repository must be invisible to GET /repos and
    GET /repos/{slug} from the instant the DELETE response lands.
    """
    admin = User(email="admin@example.com", password_hash="hashed", role=UserRole.ADMIN)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
    )
    db_session.add_all([admin, repository])
    await db_session.commit()

    await _authenticate_admin(client, settings, admin)

    class _FakePool:
        async def enqueue_job(self, *args: Any) -> None:
            return None

        async def aclose(self) -> None:
            return None

    async def _fake_create_pool(*args: Any, **kwargs: Any) -> _FakePool:
        del args, kwargs
        return _FakePool()

    monkeypatch.setattr("backend.app.api.repos.create_pool", _fake_create_pool)

    delete_response = await client.delete(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert delete_response.status_code == 204

    get_response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )
    assert get_response.status_code == 404, "soft-deleted repos must 404 from slug GET"

    list_response = await client.get("/api/repos")
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    repo_ids = {item["id"] for item in items}
    assert str(repository.id) not in repo_ids, (
        "soft-deleted repos must not appear in the list endpoint"
    )


# ---------------------------------------------------------------------------
# Enrichment: readme / description / language_bytes / documents_count
# ---------------------------------------------------------------------------


async def test_get_repository_documents_count_from_db(client, db_session):
    """documents_count must reflect actual repo_documents rows, not a hardcoded 0."""
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/docs-test.git",
        name="docs-test",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repository)
    await db_session.commit()

    # Add two repo documents.
    for i in range(2):
        doc = RepoDocument(
            repository_id=repository.id,
            file_path=f"docs/page{i}.md",
            title=f"Page {i}",
            content=f"# Page {i}\n\nContent here.",
            content_hash=f"hash{i}",
            bytes=50,
        )
        db_session.add(doc)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    assert response.json()["stats"]["documents_count"] == 2


async def test_get_repository_language_bytes_populated(client, db_session):
    """stats.language_bytes mirrors the persisted full-repo scan (issue #66)."""
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/lang-test.git",
        name="lang-test",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        language_bytes={"go": 9500, "javascript": 480, "makefile": 20},
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    stats = response.json()["stats"]
    assert stats["language_bytes"] == {"go": 9500, "javascript": 480, "makefile": 20}
    # languages are ordered by bytes DESC, so the chart and the tag list agree.
    assert stats["languages"] == ["go", "javascript", "makefile"]


async def test_get_repository_language_bytes_none_when_unscanned(client, db_session):
    """language_bytes is None (omitted) until the sync pipeline has scanned."""
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/empty-lang.git",
        name="empty-lang",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    assert response.json()["stats"]["language_bytes"] is None
    assert response.json()["stats"]["languages"] == []


async def test_get_repository_readme_populated_from_repo_documents(client, db_session):
    """readme field is populated when a README.md repo_document exists."""
    from backend.app.models.repo_document import RepoDocument

    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/readme-test.git",
        name="readme-test",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repository)
    await db_session.commit()

    readme_content = "# My Repo\n\nA great project that does things."
    doc = RepoDocument(
        repository_id=repository.id,
        file_path="README.md",
        title="README",
        content=readme_content,
        content_hash="readmehash",
        bytes=len(readme_content),
    )
    db_session.add(doc)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["readme"] == readme_content
    # description should be extracted from the README (first non-heading paragraph)
    assert data["description"] == "A great project that does things."


async def test_get_repository_readme_none_when_no_readme_document(client, db_session):
    """readme field is null when no README-like document exists."""
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/no-readme.git",
        name="no-readme",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    assert response.json()["readme"] is None
    assert response.json()["description"] is None


# ---------------------------------------------------------------------------
# GET /api/repos filters: search and status
# ---------------------------------------------------------------------------


async def test_list_repositories_search_by_name(client, db_session):
    """search param filters repos by name (case-insensitive)."""
    repos = [
        Repository(
            host="github.com",
            git_url="https://github.com/acme/alpha.git",
            name="alpha",
            owner="acme",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        ),
        Repository(
            host="github.com",
            git_url="https://github.com/acme/beta.git",
            name="beta",
            owner="acme",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        ),
        Repository(
            host="github.com",
            git_url="https://github.com/acme/gamma.git",
            name="gamma",
            owner="acme",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        ),
    ]
    db_session.add_all(repos)
    await db_session.commit()

    response = await client.get("/api/repos?search=alph")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "alpha"


async def test_list_repositories_search_by_owner(client, db_session):
    """search param matches against owner field."""
    repos = [
        Repository(
            host="github.com",
            git_url="https://github.com/acme/repo1.git",
            name="repo1",
            owner="acme",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        ),
        Repository(
            host="github.com",
            git_url="https://github.com/other/repo2.git",
            name="repo2",
            owner="other",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        ),
    ]
    db_session.add_all(repos)
    await db_session.commit()

    response = await client.get("/api/repos?search=other")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["owner"] == "other"


async def test_list_repositories_search_empty_string_returns_all(client, db_session):
    """Empty search string is a no-op — returns all repos."""
    repos = [
        Repository(
            host="github.com",
            git_url=f"https://github.com/acme/r{i}.git",
            name=f"r{i}",
            owner="acme",
            branch="main",
            visibility=RepositoryVisibility.PUBLIC,
        )
        for i in range(3)
    ]
    db_session.add_all(repos)
    await db_session.commit()

    response = await client.get("/api/repos?search=")

    assert response.status_code == 200
    assert response.json()["total"] == 3


async def test_list_repositories_filter_by_status(client, db_session):
    """status param filters repos by RepositoryStatus."""
    pending_repo = Repository(
        host="example.com",
        git_url="https://github.com/acme/pending.git",
        name="pending",
        owner="acme",
        branch="main",
        status=RepositoryStatus.PENDING,
        visibility=RepositoryVisibility.PUBLIC,
    )
    ready_repo = Repository(
        host="example.com",
        git_url="https://github.com/acme/ready.git",
        name="ready",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
    )
    db_session.add_all([pending_repo, ready_repo])
    await db_session.commit()

    response = await client.get("/api/repos?status=ready")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "ready"


async def test_list_repositories_filter_by_status_no_match(client, db_session):
    """status filter returns empty list when no repos have that status."""
    repo = Repository(
        host="example.com",
        git_url="https://github.com/acme/pending2.git",
        name="pending2",
        owner="acme",
        branch="main",
        status=RepositoryStatus.PENDING,
    )
    db_session.add(repo)
    await db_session.commit()

    response = await client.get("/api/repos?status=ready")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


# ---------------------------------------------------------------------------
# Idempotency-Key on POST /api/repos
# ---------------------------------------------------------------------------


async def test_create_repository_idempotency_key_replay_returns_same_payload(
    client, app, db_session, settings
):
    """Second POST with the same Idempotency-Key must return the same 202 payload."""
    admin = User(email="idem@example.com", password_hash="hashed", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.commit()
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

    idem_key = "test-idempotency-key-12345"

    # First request — creates the repo and stores the idempotency record.
    r1 = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/idem-test.git"},
        headers={"X-CSRF-Token": _TEST_CSRF, "Idempotency-Key": idem_key},
    )
    assert r1.status_code == 202
    first_body = r1.json()
    first_repo_id = first_body["id"]

    # Second request with the same key — replay, must return the same body.
    r2 = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/idem-test.git"},
        headers={"X-CSRF-Token": _TEST_CSRF, "Idempotency-Key": idem_key},
    )
    assert r2.status_code == 202
    second_body = r2.json()

    app.dependency_overrides.clear()

    assert second_body["id"] == first_repo_id, "Replay must return same repository id"
    assert second_body["name"] == first_body["name"]

    # Orchestrator must only have been called once (replay skips re-creation).
    assert len(orchestrator.calls) == 1


async def test_create_repository_without_idempotency_key_not_idempotent(
    client, app, db_session, settings
):
    """Without an Idempotency-Key, duplicate requests follow normal 409 conflict path."""
    admin = User(email="nokey@example.com", password_hash="hashed", role=UserRole.ADMIN)
    existing = Repository(
        host="github.com",
        git_url="https://github.com/acme/dup.git",
        name="dup",
        owner="acme",
        branch="main",
    )
    db_session.add_all([admin, existing])
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    orchestrator = _FakeOrchestrator(
        result=RepoSyncEnqueueResult(
            repository_id=uuid4(),
            sync_run_id=uuid4(),
            batch_id=None,
            status=RepoSyncRunStatus.QUEUED,
            requested_ref=None,
            deduplicated=False,
        )
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    response = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/dup.git", "branch": "main"},
        headers={"X-CSRF-Token": _TEST_CSRF},
        # No Idempotency-Key header
    )
    app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REPOSITORY_EXISTS"


# ---------------------------------------------------------------------------
# Task 1 regression: default-branch resolver used before INSERT
# ---------------------------------------------------------------------------


async def test_create_repository_duplicate_detected_when_branch_omitted(
    client, app, db_session, settings, monkeypatch
):
    """When branch is omitted, the default-branch probe runs BEFORE the INSERT.

    The IntegrityError fallback query must match the stored branch ("trunk" here),
    not None, so the second request returns REPOSITORY_EXISTS instead of generic
    409 CONFLICT.
    """
    import backend.app.api.repos as repos_module

    # Stub the remote probe to return "trunk" without touching the network.
    monkeypatch.setattr(repos_module, "_detect_default_branch", lambda _url: "trunk")

    admin = User(
        email="branchdet@example.com", password_hash="hashed", role=UserRole.ADMIN
    )
    db_session.add(admin)
    await db_session.commit()
    await _authenticate_admin(client, settings, admin)

    orchestrator = _FakeOrchestrator(
        result=RepoSyncEnqueueResult(
            repository_id=uuid4(),
            sync_run_id=uuid4(),
            batch_id=None,
            status=RepoSyncRunStatus.QUEUED,
            requested_ref=None,
            deduplicated=False,
        )
    )
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orchestrator

    # First request — creates the repo with branch="trunk".
    r1 = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/trunk-repo.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert r1.status_code == 202
    assert r1.json()["branch"] == "trunk"

    # Second request for the same URL (no explicit branch) — must return
    # REPOSITORY_EXISTS, not a generic 409 CONFLICT.
    r2 = await client.post(
        "/api/repos",
        json={"git_url": "https://github.com/acme/trunk-repo.git"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    app.dependency_overrides.clear()

    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "REPOSITORY_EXISTS", (
        "Expected REPOSITORY_EXISTS but got: " + r2.json()["error"]["code"]
    )
