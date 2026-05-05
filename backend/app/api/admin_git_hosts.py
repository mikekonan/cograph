"""Owner-managed git host catalog + operator credentials (Phase 30.5).

Mounted under `/api/admin/git-hosts`. Read endpoints are admin-or-owner;
all writes are owner-only — credential changes affect every clone, every
webhook, and every sync job.

The Test button is owner-only and runs synchronously in-request via
`httpx`; it never shells out to `gh`. Plaintext tokens are held in
memory only for the duration of the request and are never echoed back
in any response shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.audit.events import AuditEventRecord, write_audit
from backend.app.config import Settings
from backend.app.core.deps import (
    get_db_session,
    get_settings_dep,
    require_admin_or_owner,
    require_csrf,
    require_owner,
)
from backend.app.core.errors import ApiError
from backend.app.git.credentials import GitCredentialCipher, redact_token
from backend.app.models.git_credential import GitCredential
from backend.app.models.git_host import GitHost
from backend.app.models.repo_webhook_delivery import RepoWebhookDelivery
from backend.app.models.repository import Repository
from backend.app.models.user import User

router = APIRouter(prefix="/admin/git-hosts", tags=["admin", "git"])

_KINDS = {"github"}
_TEST_STATUSES = {"ok", "unauthorized", "forbidden", "network"}


# ---------------------------------------------------------------------------
# Pydantic views
# ---------------------------------------------------------------------------


class GitHostView(BaseModel):
    id: UUID
    slug: str
    display_name: str
    kind: str
    base_url: str
    api_url: str
    git_host: str
    enabled: bool
    default_credential_id: UUID | None
    created_at: datetime
    updated_at: datetime


class GitHostListResponse(BaseModel):
    hosts: list[GitHostView]


class CredentialView(BaseModel):
    id: UUID
    host_id: UUID
    label: str
    token_prefix: str
    scopes_observed: list[str] | None
    is_default: bool
    last_tested_at: datetime | None
    last_test_status: str | None
    last_test_error: str | None
    has_webhook_secret: bool
    created_at: datetime
    updated_at: datetime


class CredentialListResponse(BaseModel):
    credentials: list[CredentialView]


class CredentialTestResult(BaseModel):
    status: Literal["ok", "unauthorized", "forbidden", "network"]
    login: str | None = None
    scopes: list[str] | None = None
    error: str | None = None


class WebhookDeliveryView(BaseModel):
    id: UUID
    host_id: UUID
    delivery_id: str
    repo_full_name: str
    event: str
    received_at: datetime
    sync_job_id: str | None


class WebhookDeliveryListResponse(BaseModel):
    deliveries: list[WebhookDeliveryView]


class CreateGitHostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-_]*$")
    display_name: str = Field(min_length=1, max_length=120)
    kind: str = "github"
    base_url: str = Field(min_length=8, max_length=512)
    api_url: str = Field(min_length=8, max_length=512)
    git_host: str = Field(min_length=1, max_length=255)
    enabled: bool = True

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        if value not in _KINDS:
            raise ValueError(f"Unknown kind '{value}'. Allowed: {sorted(_KINDS)}")
        return value

    @field_validator("git_host")
    @classmethod
    def _normalise_host(cls, value: str) -> str:
        return value.strip().lower()


class UpdateGitHostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=512)
    api_url: str | None = Field(default=None, min_length=8, max_length=512)
    git_host: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None

    @field_validator("git_host")
    @classmethod
    def _normalise_host(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None


class CreateCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    token: str = Field(min_length=1, max_length=2048)
    is_default: bool = False
    webhook_secret: str | None = Field(default=None, max_length=2048)


class UpdateCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=120)
    token: str | None = Field(default=None, min_length=1, max_length=2048)
    is_default: bool | None = None
    webhook_secret: str | None = Field(default=None, max_length=2048)
    clear_webhook_secret: bool = False


class TestCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = Field(default=None, max_length=2048)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _default_credential_id(
    session: AsyncSession, host_id: UUID
) -> UUID | None:
    return await session.scalar(
        select(GitCredential.id).where(
            GitCredential.host_id == host_id,
            GitCredential.is_default.is_(True),
        )
    )


async def _host_view(session: AsyncSession, host: GitHost) -> GitHostView:
    return GitHostView(
        id=host.id,
        slug=host.slug,
        display_name=host.display_name,
        kind=host.kind,
        base_url=host.base_url,
        api_url=host.api_url,
        git_host=host.git_host,
        enabled=host.enabled,
        default_credential_id=await _default_credential_id(session, host.id),
        created_at=host.created_at,
        updated_at=host.updated_at,
    )


def _credential_view(row: GitCredential) -> CredentialView:
    return CredentialView(
        id=row.id,
        host_id=row.host_id,
        label=row.label,
        token_prefix=row.token_prefix,
        scopes_observed=list(row.scopes_observed) if row.scopes_observed else None,
        is_default=row.is_default,
        last_tested_at=row.last_tested_at,
        last_test_status=row.last_test_status,
        last_test_error=row.last_test_error,
        has_webhook_secret=bool(row.webhook_secret_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _token_prefix(plaintext: str) -> str:
    """First 12 chars of the operator PAT — enough for a humans-eye match,
    never enough to authenticate."""
    return plaintext[:12]


async def _probe_credential(
    *, host: GitHost, plaintext_token: str
) -> CredentialTestResult:
    """Hit `<api_url>/user` with the PAT. No CLI, no clone — just an HTTP
    GET so the owner sees a redacted-but-actionable error before any
    repo binds to the credential."""
    api_root = host.api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_root}/user",
                headers={
                    "Authorization": f"Bearer {plaintext_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Cograph/1.0",
                },
            )
    except httpx.HTTPError as exc:
        return CredentialTestResult(
            status="network",
            error=redact_token(str(exc), plaintext_token),
        )

    if resp.status_code == 200:
        try:
            login = resp.json().get("login")
        except ValueError:
            login = None
        scopes_header = resp.headers.get("x-oauth-scopes", "")
        scopes = [s.strip() for s in scopes_header.split(",") if s.strip()] or None
        return CredentialTestResult(status="ok", login=login, scopes=scopes)
    if resp.status_code == 401:
        return CredentialTestResult(status="unauthorized")
    if resp.status_code == 403:
        return CredentialTestResult(status="forbidden")
    return CredentialTestResult(
        status="network",
        error=f"HTTP {resp.status_code}",
    )


async def _clear_other_defaults(
    session: AsyncSession, *, host_id: UUID, keep_id: UUID
) -> None:
    """The partial unique index `WHERE is_default=TRUE` would error if two
    rows flip on in the same transaction. Clear siblings before we set
    the new default."""
    await session.execute(
        GitCredential.__table__.update()
        .where(
            GitCredential.host_id == host_id,
            GitCredential.is_default.is_(True),
            GitCredential.id != keep_id,
        )
        .values(is_default=False)
    )


# ---------------------------------------------------------------------------
# Host endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=GitHostListResponse)
async def list_git_hosts(
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_admin_or_owner),
) -> GitHostListResponse:
    rows = (
        await session.scalars(
            select(GitHost).order_by(GitHost.created_at.asc())
        )
    ).all()
    views = [await _host_view(session, row) for row in rows]
    return GitHostListResponse(hosts=views)


@router.post("", response_model=GitHostView, status_code=status.HTTP_201_CREATED)
async def create_git_host(
    payload: CreateGitHostRequest,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> GitHostView:
    del _csrf

    host = GitHost(
        slug=payload.slug,
        display_name=payload.display_name,
        kind=payload.kind,
        base_url=payload.base_url.rstrip("/"),
        api_url=payload.api_url.rstrip("/"),
        git_host=payload.git_host,
        enabled=payload.enabled,
    )
    session.add(host)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "GIT_HOST_CONFLICT",
            "A git host with this slug or hostname already exists.",
        ) from exc
    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="git_host_created",
            metadata={
                "host_id": str(host.id),
                "slug": host.slug,
                "git_host": host.git_host,
            },
        ),
    )
    await session.commit()
    await session.refresh(host)
    return await _host_view(session, host)


@router.patch("/{host_id}", response_model=GitHostView)
async def update_git_host(
    host_id: UUID,
    payload: UpdateGitHostRequest,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> GitHostView:
    del _csrf

    host = await session.get(GitHost, host_id)
    if host is None:
        raise ApiError(404, "GIT_HOST_NOT_FOUND", "Git host not found")

    changed: dict[str, object] = {}
    for field in ("display_name", "base_url", "api_url", "git_host", "enabled"):
        value = getattr(payload, field)
        if value is None:
            continue
        if field in {"base_url", "api_url"} and isinstance(value, str):
            value = value.rstrip("/")
        if getattr(host, field) != value:
            setattr(host, field, value)
            changed[field] = value

    host.updated_at = datetime.now(UTC)

    if changed:
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(
                409,
                "GIT_HOST_CONFLICT",
                "A git host with this slug or hostname already exists.",
            ) from exc
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=owner.id,
                target_user_id=None,
                event_type="git_host_updated",
                metadata={
                    "host_id": str(host.id),
                    "fields": list(changed.keys()),
                },
            ),
        )
    await session.commit()
    await session.refresh(host)
    return await _host_view(session, host)


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_git_host(
    host_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    host = await session.get(GitHost, host_id)
    if host is None:
        raise ApiError(404, "GIT_HOST_NOT_FOUND", "Git host not found")

    in_use = await session.scalar(
        select(Repository.id).where(Repository.host_id == host.id).limit(1)
    )
    if in_use is not None:
        raise ApiError(
            409,
            "HOST_IN_USE",
            "Repositories still reference this host. Remove them first or "
            "disable the host instead.",
        )

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="git_host_deleted",
            metadata={"host_id": str(host.id), "slug": host.slug},
        ),
    )
    await session.delete(host)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Credential endpoints
# ---------------------------------------------------------------------------


@router.get("/{host_id}/credentials", response_model=CredentialListResponse)
async def list_credentials(
    host_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_admin_or_owner),
) -> CredentialListResponse:
    host = await session.get(GitHost, host_id)
    if host is None:
        raise ApiError(404, "GIT_HOST_NOT_FOUND", "Git host not found")
    rows = (
        await session.scalars(
            select(GitCredential)
            .where(GitCredential.host_id == host_id)
            .order_by(GitCredential.created_at.asc())
        )
    ).all()
    return CredentialListResponse(credentials=[_credential_view(r) for r in rows])


@router.post(
    "/{host_id}/credentials",
    response_model=CredentialView,
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    host_id: UUID,
    payload: CreateCredentialRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> CredentialView:
    del _csrf

    host = await session.get(GitHost, host_id)
    if host is None:
        raise ApiError(404, "GIT_HOST_NOT_FOUND", "Git host not found")

    cipher = GitCredentialCipher(settings)
    token_encrypted = cipher.encrypt(payload.token)
    webhook_encrypted = (
        cipher.encrypt(payload.webhook_secret) if payload.webhook_secret else None
    )

    row = GitCredential(
        host_id=host.id,
        owner_user_id=owner.id,
        label=payload.label,
        token_encrypted=token_encrypted,
        token_prefix=_token_prefix(payload.token),
        is_default=payload.is_default,
        webhook_secret_encrypted=webhook_encrypted,
    )
    session.add(row)
    try:
        await session.flush()
        if payload.is_default:
            await _clear_other_defaults(session, host_id=host.id, keep_id=row.id)
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            "GIT_CREDENTIAL_CONFLICT",
            "A default credential already exists for this host.",
        ) from exc

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="git_credential_created",
            metadata={
                "host_id": str(host.id),
                "credential_id": str(row.id),
                "label": row.label,
                "is_default": row.is_default,
            },
        ),
    )
    if payload.webhook_secret:
        await write_audit(
            session,
            AuditEventRecord(
                actor_user_id=owner.id,
                target_user_id=None,
                event_type="git_webhook_secret_set",
                metadata={
                    "host_id": str(host.id),
                    "credential_id": str(row.id),
                },
            ),
        )
    await session.commit()
    await session.refresh(row)
    return _credential_view(row)


@router.patch(
    "/{host_id}/credentials/{credential_id}",
    response_model=CredentialView,
)
async def update_credential(
    host_id: UUID,
    credential_id: UUID,
    payload: UpdateCredentialRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> CredentialView:
    del _csrf

    row = await session.get(GitCredential, credential_id)
    if row is None or row.host_id != host_id:
        raise ApiError(404, "GIT_CREDENTIAL_NOT_FOUND", "Credential not found")

    cipher = GitCredentialCipher(settings)
    changed: dict[str, object] = {}
    secret_events: list[str] = []

    if payload.label is not None and payload.label != row.label:
        row.label = payload.label
        changed["label"] = payload.label

    if payload.token is not None:
        row.token_encrypted = cipher.encrypt(payload.token)
        row.token_prefix = _token_prefix(payload.token)
        # Token rotation invalidates any prior probe result.
        row.last_tested_at = None
        row.last_test_status = None
        row.last_test_error = None
        row.scopes_observed = None
        changed["token"] = "<rotated>"

    if payload.is_default is not None and payload.is_default != row.is_default:
        row.is_default = payload.is_default
        changed["is_default"] = payload.is_default
        if payload.is_default:
            await _clear_other_defaults(session, host_id=row.host_id, keep_id=row.id)

    if payload.clear_webhook_secret:
        if row.webhook_secret_encrypted is not None:
            row.webhook_secret_encrypted = None
            changed["webhook_secret"] = "<cleared>"
            secret_events.append("git_webhook_secret_cleared")
    elif payload.webhook_secret is not None:
        row.webhook_secret_encrypted = cipher.encrypt(payload.webhook_secret)
        changed["webhook_secret"] = "<rotated>"
        secret_events.append("git_webhook_secret_set")

    row.updated_at = datetime.now(UTC)

    if changed:
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(
                409,
                "GIT_CREDENTIAL_CONFLICT",
                "A default credential already exists for this host.",
            ) from exc
        if any(k != "webhook_secret" for k in changed):
            event = (
                "git_credential_set_default"
                if changed.get("is_default") is True
                else "git_credential_updated"
            )
            await write_audit(
                session,
                AuditEventRecord(
                    actor_user_id=owner.id,
                    target_user_id=None,
                    event_type=event,
                    metadata={
                        "host_id": str(row.host_id),
                        "credential_id": str(row.id),
                        "fields": [k for k in changed.keys() if k != "webhook_secret"],
                    },
                ),
            )
        for ev in secret_events:
            await write_audit(
                session,
                AuditEventRecord(
                    actor_user_id=owner.id,
                    target_user_id=None,
                    event_type=ev,
                    metadata={
                        "host_id": str(row.host_id),
                        "credential_id": str(row.id),
                    },
                ),
            )
    await session.commit()
    await session.refresh(row)
    return _credential_view(row)


@router.delete(
    "/{host_id}/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_credential(
    host_id: UUID,
    credential_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf

    row = await session.get(GitCredential, credential_id)
    if row is None or row.host_id != host_id:
        raise ApiError(404, "GIT_CREDENTIAL_NOT_FOUND", "Credential not found")

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="git_credential_deleted",
            metadata={
                "host_id": str(row.host_id),
                "credential_id": str(row.id),
                "label": row.label,
            },
        ),
    )
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{host_id}/credentials/{credential_id}/test",
    response_model=CredentialTestResult,
)
async def test_credential(
    host_id: UUID,
    credential_id: UUID,
    payload: TestCredentialRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> CredentialTestResult:
    del _csrf

    row = await session.get(GitCredential, credential_id)
    if row is None or row.host_id != host_id:
        raise ApiError(404, "GIT_CREDENTIAL_NOT_FOUND", "Credential not found")

    host = await session.get(GitHost, host_id)
    if host is None:  # pragma: no cover — FK guarantees this
        raise ApiError(404, "GIT_HOST_NOT_FOUND", "Git host not found")

    cipher = GitCredentialCipher(settings)
    plaintext = payload.token or cipher.decrypt(row.token_encrypted)

    result = await _probe_credential(host=host, plaintext_token=plaintext)

    row.last_tested_at = datetime.now(UTC)
    row.last_test_status = result.status
    row.last_test_error = result.error
    if result.status == "ok" and result.scopes is not None:
        row.scopes_observed = result.scopes
    elif result.status != "ok":
        # Don't wipe a prior good scope read on a transient failure.
        pass

    await write_audit(
        session,
        AuditEventRecord(
            actor_user_id=owner.id,
            target_user_id=None,
            event_type="git_credential_test",
            metadata={
                "host_id": str(host.id),
                "credential_id": str(row.id),
                "status": result.status,
                "login": result.login,
            },
        ),
    )
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Webhook delivery feed (read-only)
# ---------------------------------------------------------------------------


@router.get("/{host_id}/webhook-deliveries", response_model=WebhookDeliveryListResponse)
async def list_webhook_deliveries(
    host_id: UUID,
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_admin_or_owner),
) -> WebhookDeliveryListResponse:
    if limit < 1 or limit > 200:
        raise ApiError(400, "INVALID_LIMIT", "limit must be between 1 and 200")
    rows = (
        await session.scalars(
            select(RepoWebhookDelivery)
            .where(RepoWebhookDelivery.host_id == host_id)
            .order_by(desc(RepoWebhookDelivery.received_at))
            .limit(limit)
        )
    ).all()
    return WebhookDeliveryListResponse(
        deliveries=[
            WebhookDeliveryView(
                id=row.id,
                host_id=row.host_id,
                delivery_id=row.delivery_id,
                repo_full_name=row.repo_full_name,
                event=row.event,
                received_at=row.received_at,
                sync_job_id=row.sync_job_id,
            )
            for row in rows
        ]
    )
