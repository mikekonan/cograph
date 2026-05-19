"""Personal access tokens — REST + MCP unified resolver tests (Phase 30.2)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.models.enums import (
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
    UserRole,
)
from backend.app.models.md_collection import MdCollection, MdDocument
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.repository import Repository
from backend.app.models.user import User


_TEST_CSRF = "csrf-token"


async def _authenticate(client, settings, user: User) -> None:
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _TEST_CSRF}


async def _make_user(
    db_session,
    *,
    email: str = "member@example.com",
    role: UserRole = UserRole.USER,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("password-1234"),
        name=None,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def test_list_tokens_requires_authentication(client):
    response = await client.get("/api/me/tokens")
    assert response.status_code == 401


async def test_list_tokens_returns_only_own_rows(client, db_session, settings):
    member = await _make_user(db_session, email="m1@example.com")
    other = await _make_user(db_session, email="m2@example.com")
    db_session.add_all(
        [
            PersonalAccessToken(
                user_id=member.id,
                name="laptop",
                token_hash=_hash("cgr_pat_aaaa"),
                token_prefix="cgr_pat_aaaaaaaa",
                scopes=["api:read", "mcp"],
            ),
            PersonalAccessToken(
                user_id=other.id,
                name="server",
                token_hash=_hash("cgr_pat_bbbb"),
                token_prefix="cgr_pat_bbbbbbbb",
                scopes=["api:read"],
            ),
        ]
    )
    await db_session.commit()
    await _authenticate(client, settings, member)

    response = await client.get("/api/me/tokens")

    assert response.status_code == 200
    items = response.json()["tokens"]
    assert [row["name"] for row in items] == ["laptop"]
    assert items[0]["prefix"] == "cgr_pat_aaaaaaaa"
    assert items[0]["scopes"] == ["api:read", "mcp"]
    assert "token" not in items[0]


async def test_create_token_returns_plaintext_once(client, db_session, settings):
    member = await _make_user(db_session)
    await _authenticate(client, settings, member)

    response = await client.post(
        "/api/me/tokens",
        json={"name": "claude-desktop", "scopes": ["api:read", "mcp"]},
        headers=_csrf_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    plaintext = body["token"]
    view = body["view"]
    assert plaintext.startswith("cgr_pat_")
    assert view["name"] == "claude-desktop"
    assert view["prefix"] == plaintext[:16]
    assert view["scopes"] == ["api:read", "mcp"]
    assert view["last_used_at"] is None
    assert view["revoked_at"] is None

    # The list endpoint must not include plaintext.
    listed = await client.get("/api/me/tokens")
    listed_row = listed.json()["tokens"][0]
    assert "token" not in listed_row


async def test_create_token_rejects_blank_name(client, db_session, settings):
    member = await _make_user(db_session)
    await _authenticate(client, settings, member)

    response = await client.post(
        "/api/me/tokens",
        json={"name": "  ", "scopes": ["api:read"]},
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


async def test_create_token_rejects_unknown_scope(client, db_session, settings):
    member = await _make_user(db_session)
    await _authenticate(client, settings, member)

    response = await client.post(
        "/api/me/tokens",
        json={"name": "n", "scopes": ["api:read", "admin:nuke"]},
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


async def test_create_token_rejects_past_expiry(client, db_session, settings):
    member = await _make_user(db_session)
    await _authenticate(client, settings, member)

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    response = await client.post(
        "/api/me/tokens",
        json={"name": "n", "scopes": ["api:read"], "expires_at": past},
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


async def test_revoke_token_is_soft_and_user_scoped(
    client, db_session, settings
):
    member = await _make_user(db_session, email="m1@example.com")
    other = await _make_user(db_session, email="m2@example.com")
    foreign = PersonalAccessToken(
        user_id=other.id,
        name="not yours",
        token_hash=_hash("cgr_pat_xxxx"),
        token_prefix="cgr_pat_xxxxxxxx",
        scopes=["api:read"],
    )
    own = PersonalAccessToken(
        user_id=member.id,
        name="laptop",
        token_hash=_hash("cgr_pat_aaaa"),
        token_prefix="cgr_pat_aaaaaaaa",
        scopes=["api:read"],
    )
    db_session.add_all([foreign, own])
    await db_session.commit()
    await db_session.refresh(foreign)
    await db_session.refresh(own)

    await _authenticate(client, settings, member)

    resp_foreign = await client.delete(
        f"/api/me/tokens/{foreign.id}",
        headers=_csrf_headers(),
    )
    assert resp_foreign.status_code == 404

    resp_own = await client.delete(
        f"/api/me/tokens/{own.id}",
        headers=_csrf_headers(),
    )
    assert resp_own.status_code == 204

    # Soft revoke — row remains, but revoked_at and reason are populated.
    db_session.expire_all()
    rows = (await db_session.scalars(select(PersonalAccessToken))).all()
    assert len(rows) == 2
    revoked = next(row for row in rows if row.id == own.id)
    assert revoked.revoked_at is not None
    assert revoked.revoked_reason == "user"

    # Idempotent.
    again = await client.delete(
        f"/api/me/tokens/{own.id}",
        headers=_csrf_headers(),
    )
    assert again.status_code == 204


async def test_rotate_token_revokes_old_and_returns_new(client, db_session, settings):
    member = await _make_user(db_session)
    await _authenticate(client, settings, member)

    create_resp = await client.post(
        "/api/me/tokens",
        json={"name": "ci", "scopes": ["api:read", "api:write"]},
        headers=_csrf_headers(),
    )
    token_id = create_resp.json()["view"]["id"]
    plaintext_old = create_resp.json()["token"]

    rotate_resp = await client.post(
        f"/api/me/tokens/{token_id}/rotate",
        headers=_csrf_headers(),
    )
    assert rotate_resp.status_code == 201
    body = rotate_resp.json()
    assert body["token"] != plaintext_old
    assert body["token"].startswith("cgr_pat_")
    assert body["view"]["scopes"] == ["api:read", "api:write"]
    assert body["view"]["name"] == "ci"

    db_session.expire_all()
    rows = (
        await db_session.scalars(
            select(PersonalAccessToken).order_by(PersonalAccessToken.created_at.asc())
        )
    ).all()
    assert len(rows) == 2
    old_row = next(row for row in rows if str(row.id) == token_id)
    assert old_row.revoked_at is not None
    assert old_row.revoked_reason == "rotation"


async def test_pat_actor_cannot_mint_or_rotate(client, db_session, settings):
    """A token authenticated via PAT must not mint/rotate further PATs."""
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "a" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="laptop",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "api:write", "mcp"],
        )
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {plaintext}"}

    create = await client.post(
        "/api/me/tokens",
        json={"name": "nope", "scopes": ["api:read"]},
        headers=headers,
    )
    assert create.status_code == 403
    assert create.json()["error"]["code"] == "FORBIDDEN_PAT_SELF_MINT"


async def test_pat_used_against_rest_api(client, db_session, settings):
    """A live PAT authenticates plain REST GETs without cookies."""
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "b" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="laptop",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "api:write", "mcp"],
        )
    )
    await db_session.commit()

    # No cookies — bearer alone must work.
    listed = await client.get(
        "/api/me/tokens",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert listed.status_code == 200
    assert len(listed.json()["tokens"]) == 1


async def test_mcp_only_pat_cannot_read_rest_token_list(client, db_session, settings):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "m" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="mcp-only",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["mcp"],
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/me/tokens",
        headers={"Authorization": f"Bearer {plaintext}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_mcp_only_pat_cannot_read_identity_list(client, db_session, settings):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "n" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="mcp-only",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["mcp"],
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/me/identities",
        headers={"Authorization": f"Bearer {plaintext}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_mcp_only_pat_cannot_mutate_actor_endpoints(
    client, db_session, settings
):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "o" * 48
    token = PersonalAccessToken(
        user_id=member.id,
        name="mcp-only",
        token_hash=_hash(plaintext),
        token_prefix=plaintext[:16],
        scopes=["mcp"],
    )
    db_session.add(token)
    await db_session.commit()

    response = await client.delete(
        f"/api/me/tokens/{token.id}",
        headers={"Authorization": f"Bearer {plaintext}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_pat_blocked_when_user_disabled(client, db_session, settings):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "c" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="laptop",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read"],
        )
    )
    member.is_active = False
    member.deactivated_at = datetime.now(UTC)
    member.deactivated_reason = "admin"
    await db_session.commit()

    response = await client.get(
        "/api/me/tokens",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 401


async def test_pat_blocked_when_revoked(client, db_session, settings):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "d" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="laptop",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read"],
            revoked_at=datetime.now(UTC),
            revoked_reason="user",
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/me/tokens",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 401


async def test_admin_can_list_user_tokens(client, db_session, settings):
    owner = await _make_user(db_session, email="o@example.com", role=UserRole.OWNER)
    member = await _make_user(db_session, email="m@example.com")
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="laptop",
            token_hash=_hash("cgr_pat_xx"),
            token_prefix="cgr_pat_xxxxxxxx",
            scopes=["api:read"],
        )
    )
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.get(f"/api/admin/users/{member.id}/tokens")
    assert response.status_code == 200
    assert len(response.json()["tokens"]) == 1


async def test_admin_revoke_all_revokes_only_active(client, db_session, settings):
    owner = await _make_user(db_session, email="o@example.com", role=UserRole.OWNER)
    member = await _make_user(db_session, email="m@example.com")
    active = PersonalAccessToken(
        user_id=member.id,
        name="active",
        token_hash=_hash("cgr_pat_ac"),
        token_prefix="cgr_pat_active__",
        scopes=["api:read"],
    )
    revoked = PersonalAccessToken(
        user_id=member.id,
        name="revoked",
        token_hash=_hash("cgr_pat_rv"),
        token_prefix="cgr_pat_revoked_",
        scopes=["api:read"],
        revoked_at=datetime.now(UTC),
        revoked_reason="user",
    )
    db_session.add_all([active, revoked])
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.post(
        f"/api/admin/users/{member.id}/tokens/revoke-all",
        json={"reason": "admin"},
        headers=_csrf_headers(),
    )
    assert response.status_code == 200
    assert response.json()["revoked_count"] == 1

    db_session.expire_all()
    rows = (await db_session.scalars(select(PersonalAccessToken))).all()
    assert all(row.revoked_at is not None for row in rows)


async def test_mcp_endpoint_rejects_request_without_token(client):
    response = await client.post("/mcp/", json={})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "MCP_TOKEN_MISSING"


async def test_mcp_endpoint_rejects_unknown_token(client):
    response = await client.post(
        "/mcp/",
        headers={"Authorization": "Bearer cgr_pat_unknown"},
        json={},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "MCP_TOKEN_INVALID"


async def test_mcp_endpoint_rejects_token_without_mcp_scope(
    client, db_session, settings
):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "e" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="rest-only",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["api:read", "api:write"],
        )
    )
    await db_session.commit()

    response = await client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_mcp_endpoint_rejects_token_without_api_read_scope(
    client, db_session, settings
):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "f" * 48
    db_session.add(
        PersonalAccessToken(
            user_id=member.id,
            name="mcp-only",
            token_hash=_hash(plaintext),
            token_prefix=plaintext[:16],
            scopes=["mcp"],
        )
    )
    await db_session.commit()

    response = await client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_mcp_endpoint_passes_through_with_valid_token(
    client, db_session, settings
):
    member = await _make_user(db_session)
    await _authenticate(client, settings, member)
    create_resp = await client.post(
        "/api/me/tokens",
        json={"name": "ci", "scopes": ["api:read", "mcp"]},
        headers=_csrf_headers(),
    )
    plaintext = create_resp.json()["token"]

    # Drop session cookies so the MCP middleware has to recognize the bearer
    # token on its own — proves auth is independent of the JWT cookie.
    client.cookies.clear()

    response = await client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={},
    )

    # Past the auth gate. Whatever MCP responds (likely 4xx for malformed
    # JSON-RPC), it's NOT our 401 — that's all this test cares about.
    assert response.status_code != 401
    assert response.status_code != 403


async def test_mcp_repositories_hides_admin_only_repo_without_grant(
    client, db_session, settings
):
    """USER PAT without any group grant must NOT see ADMIN_ONLY repos
    through the MCP `cograph_repositories` tool. MCP funnels through
    the same `apply_repository_read_scope` as REST — so this proves
    the ACL extension propagates to MCP for free.
    """
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "g" * 48
    db_session.add_all(
        [
            PersonalAccessToken(
                user_id=member.id,
                name="mcp-read",
                token_hash=_hash(plaintext),
                token_prefix=plaintext[:16],
                scopes=["api:read", "mcp"],
            ),
            Repository(
                host="example.com",
                git_url="https://example.com/acme/private.git",
                name="private",
                owner="acme",
                branch="main",
                status=RepositoryStatus.READY,
                visibility=RepositoryVisibility.ADMIN_ONLY,
                sync_schedule=SyncSchedule.MANUAL,
            ),
        ]
    )
    await db_session.commit()

    response = await client.post(
        "/mcp/",
        headers={
            "Authorization": f"Bearer {plaintext}",
            "Accept": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "cograph_repositories",
                "arguments": {},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    result = json.loads(payload["result"]["content"][0]["text"])
    assert result["total"] == 0


async def test_mcp_repositories_lists_admin_only_repo_for_grant_holder(
    client, db_session, settings
):
    """USER PAT in a group with READ on the ADMIN_ONLY repo MUST see
    it through MCP — positive ACL counterpart for `cograph_repositories`.
    """
    from backend.app.models.enums import GrantLevel
    from backend.app.models.group import Group, GroupMember, RepositoryGrant

    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "j" * 48
    repo = Repository(
        host="example.com",
        git_url="https://example.com/acme/private.git",
        name="private",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add_all(
        [
            PersonalAccessToken(
                user_id=member.id,
                name="mcp-read",
                token_hash=_hash(plaintext),
                token_prefix=plaintext[:16],
                scopes=["api:read", "mcp"],
            ),
            repo,
        ]
    )
    await db_session.commit()

    group = Group(name="mcp-grantees")
    db_session.add(group)
    await db_session.commit()
    db_session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member.id),
            RepositoryGrant(
                group_id=group.id,
                repository_id=repo.id,
                level=GrantLevel.READ.value,
            ),
        ]
    )
    await db_session.commit()

    response = await client.post(
        "/mcp/",
        headers={
            "Authorization": f"Bearer {plaintext}",
            "Accept": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "cograph_repositories",
                "arguments": {},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    result = json.loads(payload["result"]["content"][0]["text"])
    assert result["total"] == 1
    assert result["items"][0]["slug"] == "example.com/acme/private"


async def test_mcp_collection_document_reads_private_collection_for_pat_user(
    client, db_session, settings
):
    member = await _make_user(db_session)
    plaintext = "cgr_pat_" + "h" * 48
    token = PersonalAccessToken(
        user_id=member.id,
        name="mcp-read",
        token_hash=_hash(plaintext),
        token_prefix=plaintext[:16],
        scopes=["api:read", "mcp"],
    )
    collection = MdCollection(
        name="mcp-private-collection",
        description="Private collection",
        owner_id=member.id,
        visibility="private",
    )
    db_session.add_all([token, collection])
    await db_session.flush()
    document = MdDocument(
        collection_id=collection.id,
        source_key="guide.md",
        title="Guide",
        content="# Guide\n\nPrivate collection content.",
        content_hash="hash",
        bytes=36,
        word_count=4,
        line_count=3,
        frontmatter={},
        heading_tree=[],
        code_blocks=[],
        tables=[],
        links=[],
    )
    db_session.add(document)
    await db_session.commit()

    response = await client.post(
        "/mcp/",
        headers={
            "Authorization": f"Bearer {plaintext}",
            "Accept": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "cograph_collection_document",
                "arguments": {
                    "collection_id": str(collection.id),
                    "document_id": str(document.id),
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    result = json.loads(payload["result"]["content"][0]["text"])
    assert result["collection_id"] == str(collection.id)
    assert result["source_key"] == "guide.md"
    assert result["content"].startswith("# Guide")
