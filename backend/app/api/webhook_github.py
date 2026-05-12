"""GitHub-style HMAC webhook receiver (Phase 30.5).

Mounted at `/api/webhooks/github/{host_slug}`. The path key is the host
slug, not the repo full-name — so a single per-host secret protects all
repo deliveries from that host.

Verification is constant-time HMAC SHA-256 against the per-credential
`webhook_secret_encrypted`. Once verified, the delivery row in
`repo_webhook_deliveries` (UNIQUE on `(host_id, X-GitHub-Delivery)`)
collapses retries onto one row, so GitHub's exponential-retry storm
never enqueues the same sync twice.

Repo lookup is by `(host_id, owner, name)`. If the host is registered
but the repo is not yet in Cograph, we still record the delivery (so
the dedup row protects against retry) and return 204 — operators see
the delivery in the recent-deliveries panel without it triggering work.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import (
    get_db_session,
    get_repo_sync_orchestrator,
    get_settings_dep,
)
from backend.app.core.errors import ApiError
from backend.app.git.credentials import GitCredentialCipher
from backend.app.models.enums import RepoSyncTriggerKind
from backend.app.models.git_credential import GitCredential
from backend.app.models.git_host import GitHost
from backend.app.models.repo_webhook_delivery import RepoWebhookDelivery
from backend.app.models.repository import Repository
from backend.app.pipeline.orchestrator import JobEnqueueError, RepoSyncOrchestrator

router = APIRouter(prefix="/webhooks/github", tags=["webhooks"])


class WebhookDeliveryResult(BaseModel):
    status: Literal["accepted", "duplicate", "no_repo"]
    sync_run_id: UUID | None = None


def _extract_full_name(body: bytes) -> str:
    """Pull `repository.full_name` out of the push payload, with a tolerant
    fallback for non-push events (which may not carry the field) — the
    delivery still needs to be recorded so retries dedup."""
    try:
        payload = json.loads(body or b"{}")
    except (ValueError, json.JSONDecodeError):
        return ""
    repo = payload.get("repository") if isinstance(payload, dict) else None
    if isinstance(repo, dict):
        full_name = repo.get("full_name")
        if isinstance(full_name, str):
            return full_name
        owner = repo.get("owner")
        owner_login = (
            owner.get("login") if isinstance(owner, dict) else None
        )
        name = repo.get("name")
        if isinstance(owner_login, str) and isinstance(name, str):
            return f"{owner_login}/{name}"
    return ""


async def _resolve_orchestrator(request: Request) -> RepoSyncOrchestrator:
    """Same override-aware indirection as repos.py — keeps the test
    harness honest when it swaps the orchestrator."""
    from inspect import isawaitable

    override = request.app.dependency_overrides.get(get_repo_sync_orchestrator)
    if override is not None:
        result = override()
        if isawaitable(result):
            result = await result
        return result  # type: ignore[return-value]
    return await get_repo_sync_orchestrator(request)


@router.post("/{host_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def receive_github_webhook(
    host_slug: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    host = await session.scalar(select(GitHost).where(GitHost.slug == host_slug))
    if host is None or not host.enabled:
        raise ApiError(404, "GIT_HOST_NOT_FOUND", "Git host not found")

    credential = await session.scalar(
        select(GitCredential).where(
            GitCredential.host_id == host.id,
            GitCredential.is_default.is_(True),
        )
    )
    if credential is None or credential.webhook_secret_encrypted is None:
        # 503 because the host *exists* — owner just hasn't set a webhook
        # secret yet. Distinguishes "wrong path" (404) from "config gap"
        # so GHES delivery logs surface the actionable case.
        raise ApiError(
            503,
            "WEBHOOK_SECRET_NOT_CONFIGURED",
            "Webhook secret is not configured for this host.",
        )

    cipher = GitCredentialCipher(settings)
    secret = cipher.decrypt(credential.webhook_secret_encrypted)

    body = await request.body()
    delivery_id = request.headers.get("X-GitHub-Delivery") or ""
    event = request.headers.get("X-GitHub-Event") or ""
    sig = request.headers.get("X-Hub-Signature-256") or ""

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ApiError(401, "WEBHOOK_BAD_SIGNATURE", "Invalid webhook signature")

    if not delivery_id:
        # GitHub always sets this; missing => caller is forging without
        # GitHub's plumbing. Treat as bad request rather than dedup-skip.
        raise ApiError(
            400, "WEBHOOK_MISSING_DELIVERY_ID", "X-GitHub-Delivery header required"
        )

    full_name = _extract_full_name(body)

    # Dedup insert — IntegrityError on retry is the success path.
    delivery = RepoWebhookDelivery(
        host_id=host.id,
        delivery_id=delivery_id,
        repo_full_name=full_name,
        event=event,
    )
    session.add(delivery)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Lookup repo on this host. Owner/name parse from full_name; if it's
    # blank (non-push events, or malformed) we store the dedup row but
    # don't enqueue.
    repository: Repository | None = None
    if "/" in full_name:
        owner, _, name = full_name.partition("/")
        repository = await session.scalar(
            select(Repository).where(
                Repository.host_id == host.id,
                Repository.owner == owner,
                Repository.name == name,
                # A soft-deleted repo is being torn down by the purge
                # worker — ignore inbound webhooks for it.
                Repository.deleted_at.is_(None),
            )
        )

    if repository is None:
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Only `push` events trigger a sync. ping/issue/pr/etc. are recorded
    # for visibility but don't kick the worker.
    if event != "push":
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    orchestrator = await _resolve_orchestrator(request)
    try:
        result = await orchestrator.enqueue_repository_sync(
            session=session,
            repository_id=repository.id,
            trigger_kind=RepoSyncTriggerKind.WEBHOOK,
        )
        delivery.sync_job_id = str(result.sync_run_id)
    except JobEnqueueError:
        # Drop sync_job_id silently — the dedup row is enough; owner can
        # retry from the UI. We still 204 so GitHub doesn't replay.
        pass

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
