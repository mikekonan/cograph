from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from arq import create_pool
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    field_serializer,
    field_validator,
    model_validator,
)
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import (
    get_current_user_optional,
    get_db_session,
    get_repo_sync_orchestrator,
    get_settings_dep,
    get_zip_checkout_adapter,
    require_admin,
    require_csrf,
    require_current_user,
)
from backend.app.core.errors import ApiError, FieldError
from backend.app.core.group_permissions import has_repository_permission
from backend.app.core.idempotency import (
    IdempotencyRecord,
    check_or_claim,
    mark_complete,
)
from backend.app.core.repository_access import (
    apply_repository_read_scope,
    get_readable_repository_by_slug,
)
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import (
    CodeNodeType,
    GrantLevel,
    RepoSource,
    RepoSyncRunStatus,
    RepoSyncTriggerKind,
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
)
from backend.app.models.repo_document import RepoDocument
from backend.app.models.repo_sync_run import RepoSyncRun
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile
from backend.app.models.sync_batch import SyncBatch
from backend.app.models.user import User
from backend.app.pipeline.checkout import GitCheckoutError, _detect_default_branch
from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME
from backend.app.pipeline.orchestrator import JobEnqueueError, RepoSyncOrchestrator
from backend.app.pipeline.schedule import RepoSyncScheduleService
from backend.app.pipeline.worker import build_redis_settings
from backend.app.pipeline.zip_checkout import ZipCheckoutAdapter, ZipCheckoutError

router = APIRouter(prefix="/repos", tags=["repos"])

_purge_enqueue_logger = logging.getLogger(__name__)


async def _require_repository_for_mutation(
    *,
    session: AsyncSession,
    host: str,
    owner: str,
    name: str,
    settings: Settings,
    current_user: User,
    required: GrantLevel,
) -> Repository:
    """Resolve a repo by slug AND assert the caller has `required` on it.

    Two-step gate so the 403 only fires for callers who already
    survived the 404 funnel (i.e. they could already SEE the repo):
    this avoids leaking the existence of ADMIN_ONLY repos through a
    role-tier bump on the same endpoint.
    """
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    if not await has_repository_permission(
        session, current_user, repository.id, required
    ):
        raise ApiError(403, "FORBIDDEN", "Repository access denied")
    return repository


# Regex patterns for accepted git URL forms:
# 1. https://<host>/<owner>/<repo>[.git]
_HTTPS_GIT_URL_RE = re.compile(
    r"^https://[^/\s]+/[^/\s]+/[^/\s]+",
    re.IGNORECASE,
)
# 2. git@<host>:<owner>/<repo>[.git]  (SCP-like SSH)
_SCP_SSH_GIT_URL_RE = re.compile(
    r"^git@[^:/\s]+:[^/\s]+/[^/\s]+",
    re.IGNORECASE,
)
# 3. ssh://[<user>@]<host>[:<port>]/<path>[.git]
_SSH_URL_RE = re.compile(
    r"^ssh://([^@/\s]+@)?[^/\s]+(:\d+)?/[^/\s]+/[^/\s]+",
    re.IGNORECASE,
)

# Regex patterns for README file detection (case-insensitive)
_README_FILE_RE = re.compile(
    r"^(readme)(\.md|\.rst|\.txt|\.adoc|)$",
    re.IGNORECASE,
)


class RepoStatsResponse(BaseModel):
    languages: list[str]
    language_bytes: dict[str, int] | None = None
    modules_count: int
    functions_count: int
    classes_count: int
    documents_count: int
    total_nodes: int
    source_files: int


def _fmt_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    git_url: str
    source: RepoSource = RepoSource.GIT
    host: str
    name: str
    owner: str
    branch: str
    status: str
    last_commit: str | None
    error_msg: str | None
    stats: RepoStatsResponse
    visibility: RepositoryVisibility
    sync_schedule: SyncSchedule
    log_queries: bool
    last_synced_at: datetime | None
    next_sync_at: datetime | None
    readme: str | None = None
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "last_synced_at", "next_sync_at", "created_at", "updated_at", mode="plain"
    )
    def _serialize_datetimes(self, value: datetime | None) -> str | None:
        return _fmt_dt(value)


class UpdateRepositoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_schedule: SyncSchedule | None = None
    visibility: RepositoryVisibility | None = None
    log_queries: bool | None = None

    @model_validator(mode="after")
    def _validate_non_empty_patch(self) -> "UpdateRepositoryRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one updatable repository field")
        return self


class RepoReindexResponse(BaseModel):
    id: UUID
    status: str


class RepoSyncRunResponse(BaseModel):
    id: UUID
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_msg: str | None
    requested_ref: str | None


class RepoWebhookTriggerResponse(BaseModel):
    id: UUID
    status: str


class RepositoryListResponse(BaseModel):
    items: list[RepositoryResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


class CreateRepositoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    git_url: str
    branch: str | None = None
    name: str | None = None
    sync_schedule: SyncSchedule | None = None
    visibility: RepositoryVisibility | None = None
    host_id: UUID | None = None

    @field_validator("git_url")
    @classmethod
    def validate_git_url(cls, v: str) -> str:
        # Accept SCP-like SSH: git@<host>:<owner>/<repo>[.git]
        if v.startswith("git@"):
            if not _SCP_SSH_GIT_URL_RE.match(v):
                raise ValueError(
                    "git_url must be a valid SCP-style SSH URL "
                    "(e.g. git@github.com:owner/repo.git)."
                )
            return v
        # Accept ssh:// URL form
        if v.startswith("ssh://"):
            if not _SSH_URL_RE.match(v):
                raise ValueError(
                    "git_url must be a valid SSH URL "
                    "(e.g. ssh://git@github.com/owner/repo.git)."
                )
            return v
        # Reject plain http:// — must be https
        parsed = urlparse(v)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(
                "git_url must be a valid HTTPS or SSH git URL "
                "(e.g. https://github.com/owner/repo or git@github.com:owner/repo.git)."
            )
        # Must have at least two path segments (owner + repo)
        if not _HTTPS_GIT_URL_RE.match(v):
            raise ValueError(
                "git_url must include an owner and a repository path segment "
                "(e.g. https://github.com/owner/repo)."
            )
        return v


# Slug component validation regexes — also enforced as path-param converters
# in routes and as Pydantic field validators on the ZIP upload form.
_HOST_SEGMENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,253}[A-Za-z0-9])?$")
_REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _looks_like_ssh_url(git_url: str) -> bool:
    """``git@host:owner/repo.git`` (SCP-like) or ``ssh://...``."""
    stripped = git_url.strip()
    if stripped.startswith("ssh://"):
        return True
    if stripped.startswith("git@"):
        return True
    return False


def _suggest_https_for_ssh(git_url: str) -> str | None:
    """Best-effort rewrite of an SSH URL to its HTTPS twin for the error hint."""
    stripped = git_url.strip()
    if stripped.startswith("ssh://git@"):
        rest = stripped[len("ssh://git@") :]
        if not rest:
            return None
        if "/" not in rest:
            return None
        host, _, path = rest.partition("/")
        host = host.split(":", 1)[0]
        return f"https://{host}/{path}"
    if stripped.startswith("git@") and ":" in stripped:
        host, _, path = stripped[len("git@") :].partition(":")
        if not host or not path:
            return None
        return f"https://{host}/{path}"
    return None


def _parse_host_owner_and_name(git_url: str) -> tuple[str, str, str]:
    """Return (host, owner, name) parsed from a git URL.

    Handles SCP-like SSH (`git@host:owner/repo[.git]`), HTTPS
    (`https://host/owner/repo[.git]`) and ssh:// URLs. Trailing `.git` is
    stripped. Multi-segment paths (GitLab subgroups) collapse to the last
    two segments — matches the prior parser's behaviour.
    """
    # SCP-like SSH: git@github.com:owner/repo.git
    if git_url.startswith("git@"):
        at_pos = git_url.index("@")
        colon_pos = git_url.index(":")
        host = git_url[at_pos + 1 : colon_pos]
        path = git_url[colon_pos + 1 :]
    else:
        parsed = urlparse(git_url)
        host = parsed.hostname or ""
        path = parsed.path

    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Cannot parse owner/name from git_url: {git_url!r}")
    if not host:
        raise ValueError(f"Cannot parse host from git_url: {git_url!r}")
    return host, parts[-2], parts[-1]


def _extract_description(readme_content: str) -> str | None:
    """Extract a one-liner description from the README.

    Heuristic: find the first non-heading, non-blank paragraph after the
    leading H1. Falls back to the first non-blank line if no paragraph found.
    Returns None when the content is empty or only headings/badges.
    """
    if not readme_content:
        return None

    lines = readme_content.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        # Skip badge lines (common in READMEs)
        if stripped.startswith("![") or stripped.startswith("[!["):
            continue
        # First real text line -- cap at ~200 chars to stay one-liner
        return stripped[:200] if len(stripped) > 200 else stripped or None

    return None


@router.get("", response_model=RepositoryListResponse)
async def list_repositories(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None),
    status: RepositoryStatus | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> RepositoryListResponse:
    base_q = apply_repository_read_scope(
        select(Repository),
        settings=settings,
        current_user=current_user,
    )

    # Apply search filter (ILIKE on name, owner, host, git_url)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        base_q = base_q.where(
            or_(
                Repository.name.ilike(pattern),
                Repository.owner.ilike(pattern),
                Repository.host.ilike(pattern),
                Repository.git_url.ilike(pattern),
            )
        )

    # Apply status filter
    if status is not None:
        base_q = base_q.where(Repository.status == status)

    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await session.scalar(count_q)) or 0

    rows = (
        await session.scalars(
            base_q.order_by(Repository.created_at.desc(), Repository.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()

    items = await _build_repository_list_items(session=session, repos=list(rows))
    total_pages = (total + per_page - 1) // per_page if per_page else 0
    return RepositoryListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@router.get("/{host}/{owner}/{name}", response_model=RepositoryResponse)
async def get_repository(
    host: str,
    owner: str,
    name: str,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> RepositoryResponse:
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    return await _build_repository_response(session=session, repository=repository)


@router.post(
    "", response_model=RepositoryResponse, status_code=status.HTTP_202_ACCEPTED
)
async def create_repository(
    request: Request,
    payload: CreateRepositoryRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_admin),
    _csrf: User = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RepositoryResponse | JSONResponse:
    del _csrf

    # --- Idempotency ---
    idem_record: IdempotencyRecord | None = None
    if idempotency_key:
        idem_record = await check_or_claim(
            session,
            raw_key=idempotency_key,
            user_id=current_user.id,
        )
        if idem_record.is_replay and idem_record.payload is not None:
            # Return the exact same 202 body as the original request.
            return JSONResponse(status_code=202, content=idem_record.payload)

    # Cograph clones via HTTPS + GIT_ASKPASS-injected PATs only — the
    # backend image ships ``git`` but NOT ``openssh-client``, and Phase
    # 30.5 git_credentials are HTTPS PATs by design. Reject SSH URLs
    # eagerly with a useful message instead of letting the clone fail
    # later with ``cannot run ssh: No such file or directory``.
    if _looks_like_ssh_url(payload.git_url):
        suggested = _suggest_https_for_ssh(payload.git_url)
        message = (
            "Cograph clones via HTTPS only. SSH URLs aren't supported — "
            "register the host on the Git hosts tab, add a PAT, then "
            "submit the HTTPS URL"
        )
        if suggested:
            message += f" (try: {suggested})."
        else:
            message += "."
        raise ApiError(
            422,
            "GIT_URL_SSH_UNSUPPORTED",
            message,
            field_errors=[
                FieldError(
                    field="git_url",
                    code="GIT_URL_SSH_UNSUPPORTED",
                    message=message,
                )
            ],
        )

    try:
        host, owner, name = _parse_host_owner_and_name(payload.git_url)
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_FAILED", str(exc)) from exc

    if payload.name:
        name = payload.name

    # Resolve the concrete branch BEFORE inserting so the unique constraint on
    # (git_url, branch) is evaluated against the real value, not a "main"
    # placeholder.  When the caller omits branch, probe the remote's HEAD ref;
    # fall back to "main" only if the probe times out or fails.
    if payload.branch:
        branch = payload.branch
    else:
        branch = _detect_default_branch(payload.git_url)

    # Resolve git host: explicit `host_id` wins; otherwise look up the
    # registered host (Phase 30.5) for this URL's hostname. We don't 404
    # when no host is registered — clones still work for public repos
    # via the legacy "no credential" code path. The FK is nullable
    # specifically for that case.
    from backend.app.git.credentials import resolve_host_for_url
    from backend.app.models.git_host import GitHost

    host_id_resolved: UUID | None = payload.host_id
    if host_id_resolved is not None:
        registered = await session.get(GitHost, host_id_resolved)
        if registered is None or not registered.enabled:
            raise ApiError(
                404,
                "GIT_HOST_NOT_FOUND",
                "Git host not found or disabled",
            )
        if registered.git_host.lower() != host.lower():
            raise ApiError(
                422,
                "GIT_HOST_URL_MISMATCH",
                f"git_url hostname '{host}' does not match host "
                f"'{registered.git_host}'",
            )
    else:
        registered = await resolve_host_for_url(payload.git_url, session=session)
        if registered is not None:
            host_id_resolved = registered.id

    repository = Repository(
        git_url=payload.git_url,
        host=host,
        host_id=host_id_resolved,
        name=name,
        owner=owner,
        branch=branch,
        status=RepositoryStatus.PENDING,
        visibility=payload.visibility or RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=payload.sync_schedule or SyncSchedule.MANUAL,
    )
    session.add(repository)

    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing = await session.scalar(
            select(Repository).where(
                Repository.host == host,
                Repository.owner == owner,
                Repository.name == name,
            )
        )
        if existing is not None:
            raise ApiError(
                409,
                "REPOSITORY_EXISTS",
                f"A repository with slug {host}/{owner}/{name} already exists",
                extra={
                    "host": host,
                    "owner": owner,
                    "name": name,
                    "existing_url": f"/repos/{host}/{owner}/{name}",
                },
            ) from exc
        raise ApiError(
            409, "CONFLICT", "A conflicting repository already exists"
        ) from exc

    response_obj = await _build_repository_response(
        session=session, repository=repository
    )

    # Use INITIAL trigger for the first-ever sync of this repository so the
    # JobsPage renders the correct "initial index" icon.  Subsequent reindexes
    # (triggered manually or via schedule/webhook) keep MANUAL.
    prior_batch_count: int = (
        await session.scalar(
            select(func.count(SyncBatch.id)).where(
                SyncBatch.repository_id == repository.id
            )
        )
    ) or 0
    trigger_kind = (
        RepoSyncTriggerKind.INITIAL
        if prior_batch_count == 0
        else RepoSyncTriggerKind.MANUAL
    )

    orchestrator = await _resolve_repo_sync_orchestrator(request)
    try:
        await orchestrator.enqueue_repository_sync(
            session=session,
            repository_id=repository.id,
            trigger_kind=trigger_kind,
            requested_by=current_user.id,
            auto_detect_branch=payload.branch is None,
        )
    except GitCheckoutError as exc:
        await _mark_repository_error(session, repository.id, exc)
        raise ApiError(502, "GIT_CLONE_FAILED", str(exc)) from exc
    except JobEnqueueError as exc:
        await _mark_repository_error(session, repository.id, exc)
        raise ApiError(503, "SERVICE_UNAVAILABLE", str(exc)) from exc

    # Persist idempotency payload so replays return the same body.
    if idem_record is not None and not idem_record.is_replay:
        await mark_complete(
            session,
            record_id=idem_record.record_id,
            payload=response_obj.model_dump(mode="json"),
        )
        await session.commit()

    return response_obj


_ZIP_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",  # browsers sometimes use this for .zip
        "multipart/x-zip",
    }
)


def _validate_zip_upload_filename(name: str | None) -> None:
    if not name:
        raise ApiError(422, "VALIDATION_FAILED", "Upload must include a filename")
    if not name.lower().endswith(".zip"):
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "Upload must be a .zip archive",
        )


def _derive_archive_name(filename: str | None, fallback: str) -> str:
    """Strip the `.zip` suffix and any path components to use as repo name."""
    if not filename:
        return fallback
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if base.lower().endswith(".zip"):
        base = base[: -len(".zip")]
    base = base.strip()
    return base or fallback


async def _upload_file_chunks(
    upload: UploadFile,
    *,
    chunk_size: int = 1 * 1024 * 1024,
):
    """Adapter: yield byte chunks from FastAPI's `UploadFile` for the
    streaming consumer in `ZipCheckoutAdapter.persist_upload`."""
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        yield chunk


@router.post(
    "/upload",
    response_model=RepositoryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_repository_archive(
    request: Request,
    archive: UploadFile = File(...),
    host: str = Form(""),
    owner: str = Form(""),
    name: str = Form(""),
    visibility: RepositoryVisibility | None = Form(None),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_admin),
    _csrf: User = Depends(require_csrf),
    zip_adapter: ZipCheckoutAdapter = Depends(get_zip_checkout_adapter),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RepositoryResponse | JSONResponse:
    """Create a new repository sourced from an uploaded zip archive.

    The archive is streamed to disk (`<checkouts_root>/<repo_id>.zip`),
    validated for shape and zip-bomb tells, and a sync job is enqueued
    that will re-extract the archive into the per-repo checkout dir. The
    caller supplies `host`, `owner`, `name` form fields — these become
    the repo's compound slug `host/owner/name`, the same shape git
    imports use. Subsequent regenerations MUST go through
    `POST /repos/{host}/{owner}/{name}/upload` to re-snapshot.
    """
    del _csrf

    if archive.content_type and archive.content_type.lower() not in _ZIP_CONTENT_TYPES:
        raise ApiError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            f"Unsupported archive content-type {archive.content_type!r}",
        )
    _validate_zip_upload_filename(archive.filename)

    host = host.strip()
    owner = owner.strip()
    name = name.strip()
    if not _HOST_SEGMENT_RE.match(host):
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "host must be a non-empty DNS-style segment (e.g. github.com, internal.gitlab)",
        )
    if not _REPO_SEGMENT_RE.match(owner):
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "owner must match [A-Za-z0-9._-]{1,100}",
        )
    if not _REPO_SEGMENT_RE.match(name):
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "name must match [A-Za-z0-9._-]{1,100}",
        )

    idem_record: IdempotencyRecord | None = None
    if idempotency_key:
        idem_record = await check_or_claim(
            session,
            raw_key=idempotency_key,
            user_id=current_user.id,
        )
        if idem_record.is_replay and idem_record.payload is not None:
            return JSONResponse(status_code=202, content=idem_record.payload)

    # Pre-generate the id so we commit ONCE with `git_url` already
    # populated — avoids a flush→mutate→commit dance that leaves the
    # ORM instance in a half-loaded state for downstream attribute
    # access (`updated_at`, `created_at`, …).
    repository_id = uuid4()

    # Stream + validate the archive BEFORE any DB writes so an invalid
    # upload doesn't leave a phantom Repository row behind.
    try:
        await zip_adapter.persist_upload(
            repository_id=repository_id,
            stream=_upload_file_chunks(archive),
        )
    except ZipCheckoutError as exc:
        # Best-effort filesystem cleanup of any partial write.
        await zip_adapter.discard(repository_id=repository_id)
        raise ApiError(422, "ARCHIVE_INVALID", str(exc)) from exc

    # NB: leave `last_commit` as None here. The orchestrator's dedup check
    # compares `repository.last_commit` against the prepared checkout's
    # `requested_ref` (which for zip is the archive sha256) — pre-populating
    # it to the same sha256 makes every fresh upload look like a no-op and
    # the run gets SKIPPED before any pipeline work happens. Worker will set
    # `last_commit = sha256` once the sync run finishes successfully.
    repository = Repository(
        id=repository_id,
        git_url=f"zip://{host}/{owner}/{name}",
        source=RepoSource.ZIP,
        host=host,
        name=name,
        owner=owner,
        branch="upload",
        status=RepositoryStatus.PENDING,
        last_commit=None,
        visibility=visibility or RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    session.add(repository)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        await zip_adapter.discard(repository_id=repository_id)
        existing = await session.scalar(
            select(Repository).where(
                Repository.host == host,
                Repository.owner == owner,
                Repository.name == name,
            )
        )
        if existing is not None:
            raise ApiError(
                409,
                "REPOSITORY_EXISTS",
                f"A repository with slug {host}/{owner}/{name} already exists",
                extra={
                    "host": host,
                    "owner": owner,
                    "name": name,
                    "existing_url": f"/repos/{host}/{owner}/{name}",
                },
            ) from exc
        raise ApiError(409, "CONFLICT", "Could not allocate repository row") from exc

    await session.refresh(repository)
    response_obj = await _build_repository_response(
        session=session, repository=repository
    )

    orchestrator = await _resolve_repo_sync_orchestrator(request)
    try:
        await orchestrator.enqueue_repository_sync(
            session=session,
            repository_id=repository_id,
            trigger_kind=RepoSyncTriggerKind.INITIAL,
            requested_by=current_user.id,
        )
    except ZipCheckoutError as exc:
        await _mark_repository_error(session, repository_id, exc)
        raise ApiError(422, "ARCHIVE_INVALID", str(exc)) from exc
    except JobEnqueueError as exc:
        await _mark_repository_error(session, repository_id, exc)
        raise ApiError(503, "SERVICE_UNAVAILABLE", str(exc)) from exc

    if idem_record is not None and not idem_record.is_replay:
        await mark_complete(
            session,
            record_id=idem_record.record_id,
            payload=response_obj.model_dump(mode="json"),
        )
        await session.commit()

    return response_obj


@router.post(
    "/{host}/{owner}/{name}/upload",
    response_model=RepoReindexResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_repository_archive(
    host: str,
    owner: str,
    name: str,
    request: Request,
    archive: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dep),
    zip_adapter: ZipCheckoutAdapter = Depends(get_zip_checkout_adapter),
) -> RepoReindexResponse:
    """Replace the persisted archive for a zip-source repository.

    The new zip is streamed in, validated, and an INITIAL-trigger sync
    is enqueued to re-extract + re-index. Rejected for git-source repos.
    """
    del _csrf

    repository = await _require_repository_for_mutation(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
        required=GrantLevel.WRITE,
    )
    repository_id = repository.id
    if repository.source is not RepoSource.ZIP:
        raise ApiError(
            409,
            "NOT_ZIP_SOURCED",
            "Re-upload is only supported for repositories sourced from a zip archive",
        )

    if archive.content_type and archive.content_type.lower() not in _ZIP_CONTENT_TYPES:
        raise ApiError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            f"Unsupported archive content-type {archive.content_type!r}",
        )
    _validate_zip_upload_filename(archive.filename)

    try:
        await zip_adapter.persist_upload(
            repository_id=repository_id,
            stream=_upload_file_chunks(archive),
        )
    except ZipCheckoutError as exc:
        raise ApiError(422, "ARCHIVE_INVALID", str(exc)) from exc

    # See note in `upload_repository_archive`: don't pre-populate last_commit.
    # Setting it to the new sha256 here would defeat the orchestrator's dedup
    # for non-MANUAL triggers; for MANUAL we still want the worker to be the
    # single writer of last_commit so a failed sync leaves the previous
    # successfully-synced commit visible.
    repository.status = RepositoryStatus.PENDING
    repository.error_msg = None
    await session.commit()

    orchestrator = await _resolve_repo_sync_orchestrator(request)
    try:
        result = await orchestrator.enqueue_repository_sync(
            session=session,
            repository_id=repository_id,
            trigger_kind=RepoSyncTriggerKind.MANUAL,
            requested_by=current_user.id,
        )
    except ZipCheckoutError as exc:
        await _mark_repository_error(session, repository_id, exc)
        raise ApiError(422, "ARCHIVE_INVALID", str(exc)) from exc
    except JobEnqueueError as exc:
        await _mark_repository_error(session, repository_id, exc)
        raise ApiError(503, "SERVICE_UNAVAILABLE", str(exc)) from exc

    return RepoReindexResponse(id=result.sync_run_id, status="pending")


@router.delete("/{host}/{owner}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(
    request: Request,
    host: str,
    owner: str,
    name: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_admin),
    _csrf: User = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dep),
    zip_adapter: ZipCheckoutAdapter = Depends(get_zip_checkout_adapter),
) -> None:
    del _csrf

    # Destructive op: OWNER/ADMIN role only (require_admin above).
    # Per-resource ADMIN grant was retired with GrantLevel.ADMIN; no
    # per-repo delegation today — operators must hand out OWNER/ADMIN
    # role through admin_users.
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    repository_id = repository.id
    is_zip = repository.source is RepoSource.ZIP

    # Soft-delete: flip the row's status + stamp deleted_at and return
    # 204 immediately. The actual DB cascade (HNSW-indexed embedding
    # tables, hundreds of thousands of code_edges, etc.) runs in an arq
    # worker via `purge_repository` — see
    # `backend/app/repos/purge_worker.py` for the chunked drain.
    #
    # Read-path filtering (`Repository.deleted_at.is_(None)` in
    # `apply_repository_read_scope`, the schedule scanner, webhook
    # lookup, mcp slug resolver) guarantees the row is invisible to
    # users from the instant this UPDATE commits.
    repository.status = RepositoryStatus.DELETING
    repository.deleted_at = datetime.now(UTC)
    await session.commit()

    # Zip-source archives live on the local filesystem and are cheap to
    # remove synchronously. Doing it here keeps the previous handler's
    # observable behaviour (the .zip + extracted tree are gone the
    # instant the DELETE returns) without blocking on the HNSW cascade.
    if is_zip:
        await zip_adapter.discard(repository_id=repository_id)

    await _enqueue_purge_repository(request, repository_id=repository_id)


async def _enqueue_purge_repository(request: Request, *, repository_id: UUID) -> None:
    """Drop a `purge_repository` job onto the repo-sync queue.

    Failures here are logged but not surfaced — the row is already in
    `status=DELETING` so the user sees the delete take effect, and an
    admin retry endpoint (or a fresh delete attempt) can re-enqueue.
    Mirrors the enqueue shape used by `_enqueue_md_rag_jobs` in
    `api/md_collections.py`. `create_pool` is imported on the
    module body (not inside this function) so tests can monkeypatch
    `backend.app.api.repos.create_pool` the same way they patch the
    similar import in `core.deps`.
    """
    settings = request.app.state.settings
    try:
        pool = await create_pool(
            build_redis_settings(settings.redis.url),
            default_queue_name=REPO_SYNC_QUEUE_NAME,
        )
        await pool.enqueue_job("purge_repository", str(repository_id))
        await pool.aclose()
    except Exception as exc:  # noqa: BLE001 — log and swallow, see docstring
        _purge_enqueue_logger.warning(
            "Failed to enqueue purge_repository job",
            extra={"repository_id": str(repository_id), "error": str(exc)},
        )


@router.patch("/{host}/{owner}/{name}", response_model=RepositoryResponse)
async def update_repository(
    host: str,
    owner: str,
    name: str,
    request: Request,
    payload: UpdateRepositoryRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dep),
) -> RepositoryResponse:
    del _csrf

    repository = await _require_repository_for_mutation(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
        required=GrantLevel.WRITE,
    )
    repository_id = repository.id

    if payload.sync_schedule is not None:
        if (
            repository.source is RepoSource.ZIP
            and payload.sync_schedule is not SyncSchedule.MANUAL
        ):
            raise ApiError(
                409,
                "SCHEDULE_NOT_SUPPORTED_FOR_ZIP",
                "Repositories sourced from a zip archive must use the "
                "manual sync schedule",
            )
        schedule_service = RepoSyncScheduleService()
        try:
            repository = await schedule_service.update_repository_schedule(
                session=session,
                repository_id=repository_id,
                sync_schedule=payload.sync_schedule,
            )
        except LookupError as exc:
            raise ApiError(404, "NOT_FOUND", "Repository not found") from exc

    should_commit = False
    if payload.visibility is not None:
        repository.visibility = payload.visibility
        should_commit = True
    if payload.log_queries is not None:
        repository.log_queries = payload.log_queries
        should_commit = True

    if should_commit:
        await session.commit()
        await session.refresh(repository)
        # Bust the recorder's flag cache so the operator's privacy
        # toggle takes effect immediately instead of after the
        # `query_log.repo_flag_cache_ttl_seconds` window.
        from backend.app.query_logs.recorder import invalidate_repo_log_flag_cache

        invalidate_repo_log_flag_cache(
            app_state=request.app.state,
            repository_id=repository.id,
        )

    return await _build_repository_response(session=session, repository=repository)


@router.post(
    "/{host}/{owner}/{name}/reindex",
    response_model=RepoReindexResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_repository(
    host: str,
    owner: str,
    name: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_current_user),
    _csrf: User = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dep),
) -> RepoReindexResponse:
    del _csrf
    repository = await _require_repository_for_mutation(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
        required=GrantLevel.WRITE,
    )
    if repository.source is RepoSource.ZIP:
        raise ApiError(
            409,
            "REINDEX_NOT_SUPPORTED_FOR_ZIP",
            "This repository was created from an uploaded archive. "
            "Use POST /repos/{host}/{owner}/{name}/upload to re-snapshot from a new archive.",
        )

    orchestrator = await _resolve_repo_sync_orchestrator(request)
    try:
        result = await orchestrator.enqueue_repository_sync(
            session=session,
            repository_id=repository.id,
            trigger_kind=RepoSyncTriggerKind.MANUAL,
            requested_by=current_user.id,
        )
    except GitCheckoutError as exc:
        raise ApiError(502, "GIT_CLONE_FAILED", str(exc)) from exc
    except JobEnqueueError as exc:
        raise ApiError(503, "SERVICE_UNAVAILABLE", str(exc)) from exc

    return RepoReindexResponse(
        id=result.sync_run_id,
        status="pending",
    )


@router.post(
    "/{host}/{owner}/{name}/runs/{run_id}/cancel",
    response_model=RepoSyncRunResponse,
    status_code=status.HTTP_200_OK,
)
async def cancel_repo_sync_run(
    host: str,
    owner: str,
    name: str,
    run_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_admin),
    _csrf: User = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dep),
) -> RepoSyncRunResponse:
    """Force-cancel a wedged QUEUED/RUNNING ``repo_sync_runs`` row.

    Admin-only because a wrongful cancel clobbers in-flight indexing.
    The endpoint resolves the repo via the standard slug check, asserts
    the run actually belongs to this repo (otherwise 404 to avoid leaking
    run IDs across repos), and delegates to ``orchestrator.cancel_run``
    which handles the ARQ abort + DB cascade + audit row in a single
    transaction.
    """
    del _csrf
    repository = await get_readable_repository_by_slug(
        session=session,
        host=host,
        owner=owner,
        name=name,
        settings=settings,
        current_user=current_user,
    )
    run = await session.get(RepoSyncRun, run_id)
    if run is None or run.repository_id != repository.id:
        raise ApiError(404, "NOT_FOUND", "Run not found")
    if run.status not in (RepoSyncRunStatus.QUEUED, RepoSyncRunStatus.RUNNING):
        raise ApiError(
            409,
            "INVALID_STATE",
            f"Cannot cancel run in status {run.status.value}",
        )
    orchestrator = await _resolve_repo_sync_orchestrator(request)
    await orchestrator.cancel_run(
        session=session,
        run_id=run_id,
        actor_user_id=current_user.id,
        reason="Cancelled by admin.",
    )
    await session.refresh(run)
    return RepoSyncRunResponse(
        id=run.id,
        status=run.status.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_code=run.error_code,
        error_msg=run.error_msg,
        requested_ref=run.requested_ref,
    )


@router.post(
    "/{host}/{owner}/{name}/webhook",
    response_model=RepoWebhookTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_repository_webhook(
    host: str,
    owner: str,
    name: str,
    request: Request,
    x_cograph_webhook_secret: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> RepoWebhookTriggerResponse:
    repository = await session.scalar(
        select(Repository).where(
            Repository.host == host,
            Repository.owner == owner,
            Repository.name == name,
            # A soft-deleted repo is being purged in the background;
            # webhook hits for it look identical to "no such repo".
            Repository.deleted_at.is_(None),
        )
    )
    if repository is None:
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    repository_id = repository.id
    if repository.source is RepoSource.ZIP:
        raise ApiError(
            409,
            "WEBHOOK_DISABLED",
            "Webhooks are not supported for repositories sourced from a zip archive",
        )
    if (
        repository.sync_schedule is not SyncSchedule.WEBHOOK
        or not repository.webhook_secret
    ):
        raise ApiError(
            409, "WEBHOOK_DISABLED", "Webhook sync is not enabled for this repository"
        )
    if x_cograph_webhook_secret is None or not secrets.compare_digest(
        x_cograph_webhook_secret,
        repository.webhook_secret,
    ):
        raise ApiError(403, "FORBIDDEN", "Invalid webhook secret")

    orchestrator = await _resolve_repo_sync_orchestrator(request)
    try:
        result = await orchestrator.enqueue_repository_sync(
            session=session,
            repository_id=repository_id,
            trigger_kind=RepoSyncTriggerKind.WEBHOOK,
        )
    except GitCheckoutError as exc:
        raise ApiError(502, "GIT_CLONE_FAILED", str(exc)) from exc
    except JobEnqueueError as exc:
        raise ApiError(503, "SERVICE_UNAVAILABLE", str(exc)) from exc

    return RepoWebhookTriggerResponse(
        id=result.sync_run_id,
        status=_sync_response_status(result.status),
    )


async def _mark_repository_error(
    session: AsyncSession,
    repository_id: UUID,
    exc: Exception,
) -> None:
    """Ensure the Repository row is marked ERROR after a failed enqueue."""
    repo = await session.get(Repository, repository_id)
    if repo is not None and repo.status is not RepositoryStatus.ERROR:
        repo.status = RepositoryStatus.ERROR
        repo.error_msg = str(exc)
        await session.commit()


async def _build_repository_response(
    *,
    session: AsyncSession,
    repository: Repository,
) -> RepositoryResponse:
    # 1. CodeNode aggregates
    aggregate_row = (
        await session.execute(
            select(
                func.count(CodeNode.id).label("total_nodes"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CodeNode.node_type.in_(
                                    (CodeNodeType.FUNCTION, CodeNodeType.METHOD)
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("functions_count"),
                func.coalesce(
                    func.sum(
                        case((CodeNode.node_type == CodeNodeType.CLASS, 1), else_=0)
                    ),
                    0,
                ).label("classes_count"),
                func.coalesce(
                    func.sum(
                        case((CodeNode.node_type == CodeNodeType.MODULE, 1), else_=0)
                    ),
                    0,
                ).label("modules_count"),
            ).where(CodeNode.repository_id == repository.id)
        )
    ).one()

    # Issue #66 — language_bytes is the full-repo scan persisted by the sync
    # pipeline. `languages` is just its keys ordered by share, so the bar
    # chart and the tag list always agree on what's in the repo.
    language_bytes: dict[str, int] | None = (
        {str(k): int(v) for k, v in repository.language_bytes.items() if int(v) > 0}
        if repository.language_bytes
        else None
    )
    languages: list[str] = (
        sorted(language_bytes.keys(), key=lambda k: language_bytes[k], reverse=True)
        if language_bytes
        else []
    )

    # 3. documents_count -- real count from repo_documents
    documents_count: int = (
        await session.scalar(
            select(func.count(RepoDocument.id)).where(
                RepoDocument.repository_id == repository.id
            )
        )
    ) or 0

    # 4. source_files count
    source_files_count: int = (
        await session.scalar(
            select(func.count(SourceFile.id)).where(
                SourceFile.repository_id == repository.id
            )
        )
    ) or 0

    # 5. README -- first repo_document whose file_path basename matches README pattern
    readme_content: str | None = None
    readme_rows = (
        await session.execute(
            select(RepoDocument.file_path, RepoDocument.content).where(
                RepoDocument.repository_id == repository.id
            )
        )
    ).all()
    for row in readme_rows:
        basename = row.file_path.rsplit("/", 1)[-1]
        if _README_FILE_RE.match(basename):
            readme_content = row.content
            break

    description: str | None = (
        _extract_description(readme_content) if readme_content else None
    )

    return RepositoryResponse(
        id=repository.id,
        git_url=repository.git_url,
        source=repository.source,
        host=repository.host,
        name=repository.name,
        owner=repository.owner,
        branch=repository.branch,
        status=repository.status.value,
        last_commit=repository.last_commit,
        error_msg=repository.error_msg,
        stats=RepoStatsResponse(
            languages=languages,
            language_bytes=language_bytes,
            modules_count=int(aggregate_row.modules_count or 0),
            functions_count=int(aggregate_row.functions_count or 0),
            classes_count=int(aggregate_row.classes_count or 0),
            documents_count=documents_count,
            total_nodes=int(aggregate_row.total_nodes or 0),
            source_files=source_files_count,
        ),
        visibility=repository.visibility,
        sync_schedule=repository.sync_schedule,
        log_queries=repository.log_queries,
        last_synced_at=repository.last_synced_at,
        next_sync_at=repository.next_sync_at,
        readme=readme_content,
        description=description,
        created_at=repository.created_at,
        updated_at=repository.updated_at,
    )


async def _build_repository_list_items(
    session: AsyncSession,
    repos: list[Repository],
) -> list[RepositoryResponse]:
    """Build RepositoryResponse objects for a page of repos using batch queries.

    language_bytes and readme are NOT populated on list responses (detail-only per FE_CONTRACT).
    """
    if not repos:
        return []

    repo_ids = [r.id for r in repos]

    # --- Batch aggregates (1 query) ---
    aggregate_rows = (
        await session.execute(
            select(
                CodeNode.repository_id,
                func.count(CodeNode.id).label("total_nodes"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CodeNode.node_type.in_(
                                    (CodeNodeType.FUNCTION, CodeNodeType.METHOD)
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("functions_count"),
                func.coalesce(
                    func.sum(
                        case((CodeNode.node_type == CodeNodeType.CLASS, 1), else_=0)
                    ),
                    0,
                ).label("classes_count"),
                func.coalesce(
                    func.sum(
                        case((CodeNode.node_type == CodeNodeType.MODULE, 1), else_=0)
                    ),
                    0,
                ).label("modules_count"),
            )
            .where(CodeNode.repository_id.in_(repo_ids))
            .group_by(CodeNode.repository_id)
        )
    ).all()

    agg_by_id: dict[UUID, Any] = {row.repository_id: row for row in aggregate_rows}

    # --- Batch languages (in-memory; sourced from repository.language_bytes) ---
    # Issue #66 — derive the tag list from the persisted full-repo scan so
    # cards show the same composition as the detail page's bar chart.
    langs_by_id: dict[UUID, list[str]] = {}
    for repo in repos:
        bytes_map = repo.language_bytes or {}
        if not bytes_map:
            continue
        langs_by_id[repo.id] = sorted(
            (str(k) for k, v in bytes_map.items() if int(v) > 0),
            key=lambda k: int(bytes_map[k]),
            reverse=True,
        )

    # --- Batch documents_count (1 query) ---
    doc_count_rows = (
        await session.execute(
            select(
                RepoDocument.repository_id,
                func.count(RepoDocument.id).label("doc_count"),
            )
            .where(RepoDocument.repository_id.in_(repo_ids))
            .group_by(RepoDocument.repository_id)
        )
    ).all()
    doc_count_by_id: dict[UUID, int] = {
        row.repository_id: int(row.doc_count) for row in doc_count_rows
    }

    # --- Batch source_files count (1 query) ---
    sf_count_rows = (
        await session.execute(
            select(
                SourceFile.repository_id,
                func.count(SourceFile.id).label("sf_count"),
            )
            .where(SourceFile.repository_id.in_(repo_ids))
            .group_by(SourceFile.repository_id)
        )
    ).all()
    sf_count_by_id: dict[UUID, int] = {
        row.repository_id: int(row.sf_count) for row in sf_count_rows
    }

    # --- Assemble responses ---
    items: list[RepositoryResponse] = []
    for repo in repos:
        agg = agg_by_id.get(repo.id)
        items.append(
            RepositoryResponse(
                id=repo.id,
                git_url=repo.git_url,
                source=repo.source,
                host=repo.host,
                name=repo.name,
                owner=repo.owner,
                branch=repo.branch,
                status=repo.status.value,
                last_commit=repo.last_commit,
                error_msg=repo.error_msg,
                stats=RepoStatsResponse(
                    languages=langs_by_id.get(repo.id, []),
                    language_bytes=None,
                    modules_count=int(agg.modules_count if agg else 0),
                    functions_count=int(agg.functions_count if agg else 0),
                    classes_count=int(agg.classes_count if agg else 0),
                    documents_count=doc_count_by_id.get(repo.id, 0),
                    source_files=sf_count_by_id.get(repo.id, 0),
                    total_nodes=int(agg.total_nodes if agg else 0),
                ),
                visibility=repo.visibility,
                sync_schedule=repo.sync_schedule,
                log_queries=repo.log_queries,
                last_synced_at=repo.last_synced_at,
                next_sync_at=repo.next_sync_at,
                readme=None,
                description=None,
                created_at=repo.created_at,
                updated_at=repo.updated_at,
            )
        )
    return items


def _sync_response_status(status_value: RepoSyncRunStatus) -> str:
    if status_value in (RepoSyncRunStatus.QUEUED, RepoSyncRunStatus.RUNNING):
        return "pending"
    return status_value.value


async def _resolve_repo_sync_orchestrator(request: Request) -> RepoSyncOrchestrator:
    override = request.app.dependency_overrides.get(get_repo_sync_orchestrator)
    if override is not None:
        result = override()
        if isawaitable(result):
            result = await result
        assert isinstance(result, RepoSyncOrchestrator) or hasattr(
            result,
            "enqueue_repository_sync",
        )
        return result
    return await get_repo_sync_orchestrator(request)
