"""ACL-mutation tests for `/api/repos` endpoints.

Proves the new contract on repos mutation endpoints:

* USER-tier callers no longer get implicit admin power from being
  authenticated. They need a per-(group, repo) WRITE grant to reindex /
  PATCH / upload, and an ADMIN grant to DELETE.
* OWNER/ADMIN role short-circuit still applies (covered by the
  existing tests in `test_repos_api.py` — repeated here only for the
  delta cases).
* 403 vs 404 split: a USER who can SEE the repo (PUBLIC, or read
  grant) but lacks the required grant level gets 403. A USER who
  cannot even see the repo (ADMIN_ONLY without any grant) gets 404 —
  same existence-leak guard as the read-side funnel.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from backend.app.core.auth import TokenType, create_token
from backend.app.models.enums import (
    GrantLevel,
    RepoSyncRunStatus,
    RepositoryVisibility,
    UserRole,
)
from backend.app.models.group import Group, GroupMember, RepositoryGrant
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.pipeline.orchestrator import RepoSyncEnqueueResult
from backend.app.core.deps import get_repo_sync_orchestrator


_TEST_CSRF = "csrf-token"


def _hash_pat(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


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


async def _make_user(db_session, *, role: UserRole = UserRole.USER) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _make_repo(
    db_session, *, visibility: RepositoryVisibility = RepositoryVisibility.ADMIN_ONLY
) -> Repository:
    repo = Repository(
        host="example.com",
        git_url=f"https://example.com/acme/r-{uuid4().hex[:6]}.git",
        name=f"r-{uuid4().hex[:6]}",
        owner="acme",
        branch="main",
        visibility=visibility,
    )
    db_session.add(repo)
    await db_session.commit()
    return repo


async def _grant_repo(
    db_session, *, user: User, repo: Repository, level: GrantLevel
) -> None:
    group = Group(name=f"g-{uuid4().hex[:8]}")
    db_session.add(group)
    await db_session.commit()
    db_session.add(GroupMember(group_id=group.id, user_id=user.id))
    db_session.add(
        RepositoryGrant(
            group_id=group.id, repository_id=repo.id, level=level.value
        )
    )
    await db_session.commit()


class _FakeOrch:
    def __init__(self, sync_run_id) -> None:
        self._sync_run_id = sync_run_id
        self.calls: list[dict] = []

    async def enqueue_repository_sync(self, **kwargs):
        self.calls.append(kwargs)
        return RepoSyncEnqueueResult(
            repository_id=kwargs["repository_id"],
            sync_run_id=self._sync_run_id,
            batch_id=None,
            status=RepoSyncRunStatus.QUEUED,
            requested_ref="abc",
            deduplicated=False,
        )


# ----- reindex (WRITE) -----------------------------------------------------


async def test_reindex_denied_for_user_without_grant(
    client, db_session, settings
):
    """USER without any group grant on an ADMIN_ONLY repo: 404 (funnel
    hides the repo's existence before we even get to the grant check).
    """
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _auth_user(client, settings, user)

    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/reindex",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 404


async def test_reindex_denied_for_user_with_only_read_grant(
    client, db_session, settings
):
    """USER with READ on an ADMIN_ONLY repo CAN see it (404→403 path)
    but lacks WRITE, so reindex is 403 — proves the ladder gate.
    """
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.READ)
    await _auth_user(client, settings, user)

    response = await client.post(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/reindex",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_reindex_allowed_for_user_with_write_grant(
    client, app, db_session, settings
):
    """USER with WRITE on an ADMIN_ONLY repo can reindex. Positive
    counterpart that proves the ACL extension reaches the reindex
    handler."""
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.WRITE)
    await _auth_user(client, settings, user)

    sync_run_id = uuid4()
    orch = _FakeOrch(sync_run_id)
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orch

    try:
        response = await client.post(
            f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/reindex",
            headers={"X-CSRF-Token": _TEST_CSRF},
        )
    finally:
        app.dependency_overrides.pop(get_repo_sync_orchestrator, None)

    assert response.status_code == 202
    assert response.json()["id"] == str(sync_run_id)
    assert orch.calls[0]["requested_by"] == user.id


# ----- PATCH (WRITE) -------------------------------------------------------


async def test_patch_repository_denied_for_user_with_only_read_grant(
    client, db_session, settings
):
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.READ)
    await _auth_user(client, settings, user)

    response = await client.patch(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}",
        json={"visibility": "public"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_patch_repository_allowed_for_user_with_write_grant(
    client, db_session, settings
):
    """WRITE-grantee can flip visibility — proves the ACL extension
    reaches the PATCH handler's metadata-update path.
    """
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.WRITE)
    await _auth_user(client, settings, user)

    response = await client.patch(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}",
        json={"visibility": "public"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 200
    assert response.json()["visibility"] == "public"


# ----- DELETE (ADMIN) ------------------------------------------------------


async def test_delete_repository_denied_for_user_with_write_grant(
    client, db_session, settings
):
    """WRITE is one rung below ADMIN — must NOT satisfy DELETE.
    Catches a future regression where the ladder comparison is
    flipped or the wrong level is passed to the helper.
    """
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.WRITE)
    await _auth_user(client, settings, user)

    response = await client.delete(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_delete_repository_allowed_for_user_with_admin_grant(
    client, db_session, settings, monkeypatch
):
    """ADMIN-grantee can soft-delete. We monkeypatch the purge
    enqueue helper to avoid touching Redis from a unit test.
    """
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.ADMIN)
    await _auth_user(client, settings, user)

    async def _noop_enqueue(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "backend.app.api.repos._enqueue_purge_repository", _noop_enqueue
    )

    response = await client.delete(
        f"/api/repos/{repo.host}/{repo.owner}/{repo.name}",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 204


# ----- PAT smoke (auth path goes through bearer, not cookie) ----------------


async def test_reindex_via_pat_with_write_grant_204(
    client, app, db_session, settings
):
    """End-to-end via a PAT (api:write) for a USER with WRITE grant.
    Mirrors the headless-script flow and proves the bearer-token auth
    path hits the same `_require_repository_for_mutation` gate.
    """
    user = await _make_user(db_session)
    repo = await _make_repo(db_session)
    await _grant_repo(db_session, user=user, repo=repo, level=GrantLevel.WRITE)

    plaintext = "cgr_pat_" + "w" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="ci",
            token_hash=_hash_pat(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "api:write"],
        )
    )
    await db_session.commit()

    orch = _FakeOrch(uuid4())
    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: orch

    try:
        response = await client.post(
            f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/reindex",
            headers={
                "Authorization": f"Bearer {plaintext}",
                "X-CSRF-Token": _TEST_CSRF,
            },
        )
    finally:
        app.dependency_overrides.pop(get_repo_sync_orchestrator, None)

    assert response.status_code == 202
