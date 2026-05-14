from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.models.enums import QueryLogSource, QueryLogStatus
from backend.app.pipeline.constants import REPO_SYNC_QUEUE_NAME

logger = logging.getLogger(__name__)

# Recorder-local cache for the `Repository.log_queries` flag.
#
# Avoids a SELECT-per-query when logging is on a hot path. Lives on
# `app.state.query_log_repo_flag_cache` so it is per-process and
# disappears on restart. Keyed by repository UUID → `(allow_logging,
# expires_at_monotonic)`. The PATCH endpoint busts entries on flag
# changes (via `invalidate_repo_log_flag_cache`) so the TTL only
# matters for cluster sibling processes — and they will catch up
# within the configured window.
_CACHE_STATE_ATTR = "query_log_repo_flag_cache"
_CACHE_LOCK_ATTR = "query_log_repo_flag_cache_lock"


def truncate_query_text(text: str, *, max_bytes: int) -> tuple[str, bool]:
    """Cap `text` to `max_bytes` UTF-8 bytes without splitting code points.

    Returns `(truncated_text, was_truncated)`. The boolean lets the UI
    show a "(truncated)" marker without us keeping the full text.
    """
    if text is None:
        return "", False
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    # Walk back from the byte cap until we land on a valid UTF-8 boundary.
    cut = max_bytes
    while cut > 0:
        try:
            return encoded[:cut].decode("utf-8"), True
        except UnicodeDecodeError:
            cut -= 1
    return "", True


@dataclass(slots=True)
class QueryLogPayload:
    """Wire format for the `record_query_log` arq task.

    Built on the request thread and enqueued to redis. The worker
    inserts a single row. Keep this dataclass JSON-serialisable —
    arq pickles by default but we stay portable in case the queue is
    switched later.
    """

    user_id: str | None
    user_email_snapshot: str | None
    source: str
    tool_name: str
    repository_id: str | None
    collection_id: str | None
    query_text: str
    query_truncated: bool
    top_k: int | None
    result_count: int | None
    duration_ms: int
    status: str
    error_code: str | None
    client_label: str | None

    def to_kwargs(self) -> dict[str, Any]:
        return asdict(self)


def _as_uuid_str(value: UUID | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    # Best-effort: accept str-cast UUIDs without validating here. Worker
    # validates on insert; bad strings produce a swallowed error log,
    # not a user-visible failure.
    return str(value)


def _cache_dict(app_state: Any | None) -> dict[UUID, tuple[bool, float]] | None:
    if app_state is None:
        return None
    cache = getattr(app_state, _CACHE_STATE_ATTR, None)
    if cache is None:
        cache = {}
        try:
            setattr(app_state, _CACHE_STATE_ATTR, cache)
        except Exception:
            # Some app_state stand-ins (SimpleNamespace etc.) are
            # read-only; in that case we just skip caching.
            return None
    return cache


def _cache_lock(app_state: Any | None) -> asyncio.Lock | None:
    if app_state is None:
        return None
    lock = getattr(app_state, _CACHE_LOCK_ATTR, None)
    if lock is None:
        lock = asyncio.Lock()
        try:
            setattr(app_state, _CACHE_LOCK_ATTR, lock)
        except Exception:
            return None
    return lock


def invalidate_repo_log_flag_cache(
    *,
    app_state: Any | None,
    repository_id: UUID,
) -> None:
    """Drop a cached `log_queries` decision so the next query for this
    repo re-reads the column. Called from PATCH /api/repos/{slug} when
    `log_queries` is mutated, so toggling the flag is effective
    in-process immediately."""
    cache = _cache_dict(app_state)
    if cache is None:
        return
    removed = cache.pop(repository_id, None)
    logger.info(
        "query_log: repo flag cache invalidated",
        extra={
            "repository_id": str(repository_id),
            "had_cached_entry": removed is not None,
        },
    )


async def _is_logging_allowed(
    *,
    app_state: Any | None,
    repository_id: UUID,
    ttl_seconds: int,
) -> bool:
    """Return True if the repo's `log_queries` flag is set; cache the
    decision for `ttl_seconds`.

    Defaults to True on lookup failure (missing row, DB hiccup, no
    session_manager available) — the flag is an OPT-OUT, so a
    transient infra problem must not silently drop everyone's logs.
    """
    cache = _cache_dict(app_state)
    now = time.monotonic()
    if cache is not None:
        entry = cache.get(repository_id)
        if entry is not None:
            allow, expires_at = entry
            if expires_at > now:
                logger.debug(
                    "query_log: flag cache hit",
                    extra={
                        "repository_id": str(repository_id),
                        "allow": allow,
                    },
                )
                return allow

    session_manager = (
        getattr(app_state, "session_manager", None) if app_state is not None else None
    )
    if session_manager is None:
        logger.debug(
            "query_log: no session_manager on app_state; defaulting to allow",
            extra={"repository_id": str(repository_id)},
        )
        return True

    lock = _cache_lock(app_state)
    # Lock so concurrent first-hits don't all open a session.
    if lock is not None:
        await lock.acquire()
    try:
        if cache is not None:
            entry = cache.get(repository_id)
            if entry is not None and entry[1] > now:
                return entry[0]
        try:
            from backend.app.models.repository import Repository

            async with session_manager.session() as session:
                allow = await session.scalar(
                    select(Repository.log_queries).where(Repository.id == repository_id)
                )
            if allow is None:
                # Repo gone / not yet visible — don't cache; treat as
                # allowed so the row still gets written (the FK is
                # SET NULL so a phantom repo_id won't break insert).
                logger.debug(
                    "query_log: repo row not found, defaulting to allow",
                    extra={"repository_id": str(repository_id)},
                )
                return True
            allow_bool = bool(allow)
        except Exception:
            logger.warning(
                "Failed to read Repository.log_queries; allowing log",
                exc_info=True,
                extra={"repository_id": str(repository_id)},
            )
            return True
        if cache is not None and ttl_seconds > 0:
            cache[repository_id] = (allow_bool, now + ttl_seconds)
        logger.debug(
            "query_log: flag cache miss, value cached",
            extra={
                "repository_id": str(repository_id),
                "allow": allow_bool,
                "ttl_seconds": ttl_seconds,
            },
        )
        return allow_bool
    finally:
        if lock is not None:
            lock.release()


async def _get_pool(app_state: Any | None) -> ArqRedis:
    """Re-use the request's `app.state.arq_pool` if it's there, else
    open a fresh pool. The pool is connection-pooled internally so
    repeated `create_pool` calls are cheap, but reusing avoids the
    1–2ms handshake on every request.
    """
    pool = getattr(app_state, "arq_pool", None) if app_state is not None else None
    if isinstance(pool, ArqRedis):
        return pool
    from backend.app.pipeline.worker import build_redis_settings

    settings = get_settings()
    return await create_pool(
        build_redis_settings(settings.redis.url),
        default_queue_name=REPO_SYNC_QUEUE_NAME,
    )


async def enqueue_query_log(
    *,
    app_state: Any | None,
    user_id: UUID | None,
    user_email: str | None,
    source: QueryLogSource,
    tool_name: str,
    query_text: str,
    repository_id: UUID | None = None,
    collection_id: UUID | None = None,
    top_k: int | None = None,
    result_count: int | None = None,
    duration_ms: int,
    status: QueryLogStatus,
    error_code: str | None = None,
    client_label: str | None = None,
) -> None:
    """Enqueue one query_log row for async insert.

    Never raises — a logging-system failure must not break the user's
    query. The worst case is a missing row.

    `app_state` is the FastAPI `request.app.state` (or `None` for MCP
    handlers that don't have a Starlette request); used to reach the
    arq pool initialised at app startup.
    """
    settings = get_settings()
    if settings.query_log.disabled:
        logger.debug(
            "query_log: kill switch on (query_log.disabled=true); skipping",
            extra={"tool_name": tool_name, "source": source.value},
        )
        return

    if repository_id is not None:
        allowed = await _is_logging_allowed(
            app_state=app_state,
            repository_id=repository_id,
            ttl_seconds=settings.query_log.repo_flag_cache_ttl_seconds,
        )
        if not allowed:
            # INFO (not DEBUG): privacy-driven skip is an operator-visible
            # decision; ops will want this in the platform log when
            # answering "why isn't this repo's traffic being logged".
            logger.info(
                "query_log: skipping write — repo.log_queries=false",
                extra={
                    "tool_name": tool_name,
                    "source": source.value,
                    "repository_id": str(repository_id),
                },
            )
            return

    try:
        truncated_text, was_truncated = truncate_query_text(
            query_text or "",
            max_bytes=settings.query_log.query_text_max_bytes,
        )
        payload = QueryLogPayload(
            user_id=_as_uuid_str(user_id),
            user_email_snapshot=user_email,
            source=source.value,
            tool_name=tool_name,
            repository_id=_as_uuid_str(repository_id),
            collection_id=_as_uuid_str(collection_id),
            query_text=truncated_text,
            query_truncated=was_truncated,
            top_k=top_k,
            result_count=result_count,
            duration_ms=duration_ms,
            status=status.value,
            error_code=error_code,
            client_label=client_label,
        )
        pool = await _get_pool(app_state)
        await pool.enqueue_job("record_query_log", payload.to_kwargs())
        logger.debug(
            "query_log: enqueued",
            extra={
                "tool_name": tool_name,
                "source": source.value,
                "status": status.value,
                "repository_id": str(repository_id) if repository_id else None,
                "user_id": str(user_id) if user_id else None,
                "duration_ms": duration_ms,
                "result_count": result_count,
            },
        )
    except Exception:
        # Swallow — we do not want a logging-path failure to surface
        # to the user. Emit at WARN so operators can still notice
        # systemic breakage via the platform log.
        logger.warning(
            "Failed to enqueue query_log",
            exc_info=True,
            extra={
                "tool_name": tool_name,
                "source": source.value,
            },
        )
