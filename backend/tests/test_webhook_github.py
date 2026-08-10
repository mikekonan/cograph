"""Phase 30.5 — GitHub-style HMAC webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.app.git.credentials import GitCredentialCipher
from backend.app.models.enums import RepositoryStatus, RepositoryVisibility, SyncSchedule
from backend.app.models.git_credential import GitCredential
from backend.app.models.git_host import GitHost
from backend.app.models.repo_webhook_delivery import RepoWebhookDelivery
from backend.app.models.repository import Repository
from backend.app.models.user import User


async def _seed_host_with_credential(
    db_session,
    settings,
    *,
    slug: str = "github-com",
    git_host: str = "github.com",
    secret: str = "wh-secret",
) -> tuple[GitHost, GitCredential, User]:
    user = User(email="o@x", password_hash="x", name="o", role="owner")
    db_session.add(user)
    await db_session.flush()
    host = GitHost(
        slug=slug,
        display_name=slug,
        kind="github",
        base_url=f"https://{git_host}",
        api_url=f"https://api.{git_host}",
        git_host=git_host,
        enabled=True,
    )
    db_session.add(host)
    await db_session.flush()
    cipher = GitCredentialCipher(settings)
    cred = GitCredential(
        host_id=host.id,
        owner_user_id=user.id,
        label="ops",
        token_encrypted=cipher.encrypt("ghp_x"),
        token_prefix="ghp_x",
        is_default=True,
        webhook_secret_encrypted=cipher.encrypt(secret),
    )
    db_session.add(cred)
    await db_session.commit()
    return host, cred, user


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()


def _push_payload(owner: str = "o", name: str = "r") -> dict[str, Any]:
    return {
        "ref": "refs/heads/main",
        "repository": {
            "full_name": f"{owner}/{name}",
            "owner": {"login": owner},
            "name": name,
        },
    }


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_webhook_rejects_bad_signature(client, db_session, settings):
    host, _, _ = await _seed_host_with_credential(db_session, settings)
    body = json.dumps(_push_payload()).encode()
    resp = await client.post(
        f"/api/webhooks/github/{host.slug}",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "evt-1",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "WEBHOOK_BAD_SIGNATURE"


@pytest.mark.anyio
async def test_webhook_rejects_when_secret_unconfigured(
    client, db_session, settings
):
    user = User(email="o@x", password_hash="x", name="o", role="owner")
    db_session.add(user)
    await db_session.flush()
    host = GitHost(
        slug="github-com",
        display_name="GH",
        kind="github",
        base_url="https://github.com",
        api_url="https://api.github.com",
        git_host="github.com",
        enabled=True,
    )
    db_session.add(host)
    await db_session.flush()
    db_session.add(
        GitCredential(
            host_id=host.id,
            owner_user_id=user.id,
            label="ops",
            token_encrypted="x",
            token_prefix="ghp_x",
            is_default=True,
            webhook_secret_encrypted=None,
        )
    )
    await db_session.commit()

    body = b"{}"
    resp = await client.post(
        f"/api/webhooks/github/{host.slug}",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "evt-1",
            "X-Hub-Signature-256": _sign(body, "wh-secret"),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "WEBHOOK_SECRET_NOT_CONFIGURED"


@pytest.mark.anyio
async def test_webhook_unknown_host_returns_404(client, db_session, settings):
    body = b"{}"
    resp = await client.post(
        "/api/webhooks/github/no-such-host",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "evt-1",
            "X-Hub-Signature-256": _sign(body, "x"),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "GIT_HOST_NOT_FOUND"


# ---------------------------------------------------------------------------
# Dedup + sync enqueue
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_webhook_records_delivery_when_repo_unknown(
    client, db_session, settings
):
    """A registered host but an unknown repo: still record the delivery
    row (idempotency anchor) and 204."""
    host, _, _ = await _seed_host_with_credential(db_session, settings)
    body = json.dumps(_push_payload(name="ghost")).encode()
    resp = await client.post(
        f"/api/webhooks/github/{host.slug}",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "evt-1",
            "X-Hub-Signature-256": _sign(body, "wh-secret"),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204

    rows = (
        await db_session.execute(
            RepoWebhookDelivery.__table__.select().where(
                RepoWebhookDelivery.delivery_id == "evt-1"
            )
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_webhook_dedupes_retry_with_same_delivery_id(
    client, db_session, settings, app
):
    host, _, _ = await _seed_host_with_credential(db_session, settings)
    repo = Repository(
        git_url="https://github.com/o/r",
        host="github.com",
        host_id=host.id,
        owner="o",
        name="r",
        branch="main",
        status=RepositoryStatus.PENDING,
        visibility=RepositoryVisibility.PUBLIC,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.commit()

    fake_orchestrator = AsyncMock()
    fake_orchestrator.enqueue_repository_sync.return_value = type(
        "R", (), {"sync_run_id": uuid.uuid4(), "status": RepositoryStatus.PENDING}
    )()

    from backend.app.core.deps import get_repo_sync_orchestrator

    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: fake_orchestrator

    body = json.dumps(_push_payload()).encode()
    headers = {
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "evt-dup",
        "X-Hub-Signature-256": _sign(body, "wh-secret"),
        "Content-Type": "application/json",
    }
    first = await client.post(
        f"/api/webhooks/github/{host.slug}", content=body, headers=headers
    )
    second = await client.post(
        f"/api/webhooks/github/{host.slug}", content=body, headers=headers
    )
    assert first.status_code == 204
    assert second.status_code == 204
    # Only one enqueue despite two deliveries.
    assert fake_orchestrator.enqueue_repository_sync.call_count == 1

    rows = (
        await db_session.execute(
            RepoWebhookDelivery.__table__.select().where(
                RepoWebhookDelivery.delivery_id == "evt-dup"
            )
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_webhook_non_push_event_skips_enqueue(
    client, db_session, settings, app
):
    host, _, _ = await _seed_host_with_credential(db_session, settings)
    repo = Repository(
        git_url="https://github.com/o/r",
        host="github.com",
        host_id=host.id,
        owner="o",
        name="r",
        branch="main",
        status=RepositoryStatus.PENDING,
        visibility=RepositoryVisibility.PUBLIC,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.commit()

    fake_orchestrator = AsyncMock()
    from backend.app.core.deps import get_repo_sync_orchestrator

    app.dependency_overrides[get_repo_sync_orchestrator] = lambda: fake_orchestrator

    body = json.dumps(_push_payload()).encode()
    resp = await client.post(
        f"/api/webhooks/github/{host.slug}",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "evt-ping",
            "X-Hub-Signature-256": _sign(body, "wh-secret"),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 204
    assert fake_orchestrator.enqueue_repository_sync.call_count == 0


@pytest.mark.anyio
async def test_webhook_missing_delivery_id_400(client, db_session, settings):
    host, _, _ = await _seed_host_with_credential(db_session, settings)
    body = b"{}"
    resp = await client.post(
        f"/api/webhooks/github/{host.slug}",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _sign(body, "wh-secret"),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "WEBHOOK_MISSING_DELIVERY_ID"
