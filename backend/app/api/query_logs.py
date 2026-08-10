"""Read/write endpoints for the `query_logs` table.

Three surfaces:

- `GET /api/admin/query-logs` — admin-only, paginated browse of all
  user queries with filters (user, repo, tool, status, zero-results,
  text-search, date range). Powers the admin "Activity / Queries"
  page.
- `GET /api/admin/query-logs/stats` — admin-only aggregates over a
  date range (totals, top queries, top repos, zero-result count,
  latency percentiles).
- `GET /api/admin/query-logs/stats/users` — admin-only per-user
  activity over a date range. Includes EVERY current user, also the
  ones with zero queries — the page answers "who actually uses
  cograph", and silence is the interesting half of that answer.
- `GET /api/admin/query-logs/stats/timeseries` — admin-only
  day/hour-bucketed counts + token/cost sums for the usage chart.
- `GET /api/me/query-logs` — current user only, lists own history.
- `DELETE /api/me/query-logs` — current user only, drops *all* of the
  caller's logged queries on demand. Privacy "forget my history"
  button on the account page.

Writes are not exposed here — they happen via the arq job
`record_query_log` and are only callable from the backend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.deps import (
    get_db_session,
    require_admin_or_owner,
    require_current_user,
)
from backend.app.models.query_log import QueryLog
from backend.app.models.user import User

router = APIRouter(tags=["query-logs"])

_MAX_PAGE_SIZE = 200
_DEFAULT_PAGE_SIZE = 50


class QueryLogItem(BaseModel):
    id: UUID
    created_at: datetime
    user_id: UUID | None
    user_email: str | None
    source: str
    tool_name: str
    repository_id: UUID | None
    collection_id: UUID | None
    query_text: str
    query_truncated: bool
    top_k: int | None
    result_count: int | None
    duration_ms: int
    status: str
    error_code: str | None
    client_label: str | None
    tokens_input: int | None
    tokens_output: int | None
    cost_usd_micros: int | None
    embed_model: str | None
    completion_model: str | None


class QueryLogPage(BaseModel):
    items: list[QueryLogItem]
    total: int
    page: int
    per_page: int
    total_pages: int


class TopQueryItem(BaseModel):
    query_text: str
    count: int


class TopRepoItem(BaseModel):
    repository_id: UUID
    count: int


class QueryLogStats(BaseModel):
    total_count: int
    zero_result_count: int
    error_count: int
    p50_duration_ms: int | None
    p95_duration_ms: int | None
    top_queries: list[TopQueryItem]
    top_repos: list[TopRepoItem]
    tokens_input_total: int
    tokens_output_total: int
    cost_usd_micros_total: int
    rows_with_cost: int


class UserUsageItem(BaseModel):
    # user_id is None for rows whose user has been deleted — the email
    # snapshot is all that's left to attribute them to.
    user_id: UUID | None
    user_email: str | None
    is_active: bool | None
    is_deleted: bool
    query_count: int
    mcp_count: int
    rest_count: int
    error_count: int
    zero_result_count: int
    tokens_input: int
    tokens_output: int
    cost_usd_micros: int
    last_query_at: datetime | None


class UserUsageStats(BaseModel):
    items: list[UserUsageItem]
    total_users: int
    active_users: int


class TimeseriesBucket(BaseModel):
    bucket_start: datetime
    query_count: int
    mcp_count: int
    rest_count: int
    error_count: int
    tokens_input: int
    tokens_output: int
    cost_usd_micros: int


class UsageTimeseries(BaseModel):
    bucket: str
    since: datetime
    until: datetime
    items: list[TimeseriesBucket]


def _to_item(row: QueryLog) -> QueryLogItem:
    return QueryLogItem(
        id=row.id,
        created_at=row.created_at,
        user_id=row.user_id,
        user_email=row.user_email_snapshot,
        source=str(row.source),
        tool_name=row.tool_name,
        repository_id=row.repository_id,
        collection_id=row.collection_id,
        query_text=row.query_text,
        query_truncated=row.query_truncated,
        top_k=row.top_k,
        result_count=row.result_count,
        duration_ms=row.duration_ms,
        status=str(row.status),
        error_code=row.error_code,
        client_label=row.client_label,
        tokens_input=row.tokens_input,
        tokens_output=row.tokens_output,
        cost_usd_micros=row.cost_usd_micros,
        embed_model=row.embed_model,
        completion_model=row.completion_model,
    )


def _apply_filters(
    stmt,
    *,
    user_id: UUID | None,
    repository_id: UUID | None,
    tool_name: str | None,
    status: str | None,
    zero_results: bool | None,
    q: str | None,
    since: datetime | None,
    until: datetime | None,
):
    if user_id is not None:
        stmt = stmt.where(QueryLog.user_id == user_id)
    if repository_id is not None:
        stmt = stmt.where(QueryLog.repository_id == repository_id)
    if tool_name:
        stmt = stmt.where(QueryLog.tool_name == tool_name)
    if status:
        stmt = stmt.where(QueryLog.status == status)
    if zero_results is True:
        stmt = stmt.where(QueryLog.result_count == 0)
    if q:
        # `ilike` matches across postgres and sqlite-aiosqlite; the
        # admin page is admin-only so case-insensitive substring is the
        # expected UX without pulling in fts.
        stmt = stmt.where(QueryLog.query_text.ilike(f"%{q}%"))
    if since is not None:
        stmt = stmt.where(QueryLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(QueryLog.created_at <= until)
    return stmt


async def _paginate(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    user_id: UUID | None,
    repository_id: UUID | None,
    tool_name: str | None,
    status: str | None,
    zero_results: bool | None,
    q: str | None,
    since: datetime | None,
    until: datetime | None,
) -> QueryLogPage:
    base = _apply_filters(
        select(QueryLog),
        user_id=user_id,
        repository_id=repository_id,
        tool_name=tool_name,
        status=status,
        zero_results=zero_results,
        q=q,
        since=since,
        until=until,
    )

    total = (
        await session.scalar(
            _apply_filters(
                select(func.count(QueryLog.id)),
                user_id=user_id,
                repository_id=repository_id,
                tool_name=tool_name,
                status=status,
                zero_results=zero_results,
                q=q,
                since=since,
                until=until,
            )
        )
        or 0
    )

    rows = (
        await session.scalars(
            base.order_by(QueryLog.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).all()

    total_pages = (total + per_page - 1) // per_page if per_page else 0
    return QueryLogPage(
        items=[_to_item(r) for r in rows],
        total=int(total),
        page=page,
        per_page=per_page,
        total_pages=int(total_pages),
    )


@router.get("/admin/query-logs", response_model=QueryLogPage)
async def admin_list_query_logs(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    user_id: UUID | None = Query(default=None),
    repository_id: UUID | None = Query(default=None),
    tool_name: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(ok|empty|error)$"),
    zero_results: bool | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    _admin: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> QueryLogPage:
    return await _paginate(
        session,
        page=page,
        per_page=per_page,
        user_id=user_id,
        repository_id=repository_id,
        tool_name=tool_name,
        status=status,
        zero_results=zero_results,
        q=q,
        since=since,
        until=until,
    )


@router.get("/admin/query-logs/stats", response_model=QueryLogStats)
async def admin_query_logs_stats(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    top_n: int = Query(default=20, ge=1, le=100),
    _admin: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> QueryLogStats:
    where = _apply_filters(
        select(QueryLog.id),
        user_id=None,
        repository_id=None,
        tool_name=None,
        status=None,
        zero_results=None,
        q=None,
        since=since,
        until=until,
    ).subquery()

    total = await session.scalar(select(func.count()).select_from(where)) or 0

    zero_results = (
        await session.scalar(
            _apply_filters(
                select(func.count(QueryLog.id)),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=True,
                q=None,
                since=since,
                until=until,
            )
        )
        or 0
    )

    error_count = (
        await session.scalar(
            _apply_filters(
                select(func.count(QueryLog.id)),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status="error",
                zero_results=None,
                q=None,
                since=since,
                until=until,
            )
        )
        or 0
    )

    # Latency percentiles. PG has `percentile_cont`; sqlite (used in
    # unit tests) doesn't, so we sort + index client-side instead.
    durations = (
        await session.scalars(
            _apply_filters(
                select(QueryLog.duration_ms),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=since,
                until=until,
            ).order_by(QueryLog.duration_ms.asc())
        )
    ).all()

    def _pct(values: list[int], p: float) -> int | None:
        if not values:
            return None
        idx = max(0, min(len(values) - 1, int(round((p / 100.0) * (len(values) - 1)))))
        return int(values[idx])

    duration_values = [int(v) for v in durations]
    p50 = _pct(duration_values, 50.0)
    p95 = _pct(duration_values, 95.0)

    # Cost + token aggregates. SUMs on nullable columns coerce NULL to
    # 0 (postgres behaviour), so rows from before migration 0056 don't
    # poison the total — they simply contribute zero. `rows_with_cost`
    # lets the UI footer caveat "computed over N of M rows" so an
    # operator doesn't read a partial total as gospel.
    tokens_input_total = (
        await session.scalar(
            _apply_filters(
                select(func.coalesce(func.sum(QueryLog.tokens_input), 0)),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=since,
                until=until,
            )
        )
        or 0
    )
    tokens_output_total = (
        await session.scalar(
            _apply_filters(
                select(func.coalesce(func.sum(QueryLog.tokens_output), 0)),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=since,
                until=until,
            )
        )
        or 0
    )
    cost_total = (
        await session.scalar(
            _apply_filters(
                select(func.coalesce(func.sum(QueryLog.cost_usd_micros), 0)),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=since,
                until=until,
            )
        )
        or 0
    )
    rows_with_cost = (
        await session.scalar(
            _apply_filters(
                select(func.count(QueryLog.id)).where(
                    QueryLog.cost_usd_micros.is_not(None)
                ),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=since,
                until=until,
            )
        )
        or 0
    )

    top_queries_rows = (
        await session.execute(
            _apply_filters(
                select(
                    QueryLog.query_text,
                    func.count(QueryLog.id).label("cnt"),
                ),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=since,
                until=until,
            )
            .where(QueryLog.query_text != "")
            .group_by(QueryLog.query_text)
            .order_by(func.count(QueryLog.id).desc())
            .limit(top_n)
        )
    ).all()

    top_repos_rows = (
        await session.execute(
            _apply_filters(
                select(
                    QueryLog.repository_id,
                    func.count(QueryLog.id).label("cnt"),
                ),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=since,
                until=until,
            )
            .where(QueryLog.repository_id.is_not(None))
            .group_by(QueryLog.repository_id)
            .order_by(func.count(QueryLog.id).desc())
            .limit(top_n)
        )
    ).all()

    return QueryLogStats(
        total_count=int(total),
        zero_result_count=int(zero_results),
        error_count=int(error_count),
        p50_duration_ms=p50,
        p95_duration_ms=p95,
        top_queries=[
            TopQueryItem(query_text=row[0], count=int(row[1]))
            for row in top_queries_rows
        ],
        top_repos=[
            TopRepoItem(repository_id=row[0], count=int(row[1]))
            for row in top_repos_rows
        ],
        tokens_input_total=int(tokens_input_total),
        tokens_output_total=int(tokens_output_total),
        cost_usd_micros_total=int(cost_total),
        rows_with_cost=int(rows_with_cost),
    )


@router.get("/admin/query-logs/stats/users", response_model=UserUsageStats)
async def admin_query_logs_user_stats(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    _admin: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> UserUsageStats:
    """Per-user activity over the window — INCLUDING silent users.

    The merge is done in Python on purpose: a pure GROUP BY over
    query_logs can never produce a row for a user who ran nothing, and
    "who is NOT using cograph" is exactly what the operator opens this
    page for. User count is small (this is a self-hosted tool), so
    iterating the users table is free.
    """
    window = _apply_filters(
        select(
            QueryLog.user_id,
            func.max(QueryLog.user_email_snapshot).label("email_snapshot"),
            func.count(QueryLog.id).label("query_count"),
            func.sum(case((QueryLog.source == "mcp", 1), else_=0)).label("mcp"),
            func.sum(case((QueryLog.source == "rest", 1), else_=0)).label("rest"),
            func.sum(case((QueryLog.status == "error", 1), else_=0)).label("errors"),
            func.sum(case((QueryLog.result_count == 0, 1), else_=0)).label("zeroes"),
            func.coalesce(func.sum(QueryLog.tokens_input), 0).label("tok_in"),
            func.coalesce(func.sum(QueryLog.tokens_output), 0).label("tok_out"),
            func.coalesce(func.sum(QueryLog.cost_usd_micros), 0).label("cost"),
            func.max(QueryLog.created_at).label("last_at"),
        ),
        user_id=None,
        repository_id=None,
        tool_name=None,
        status=None,
        zero_results=None,
        q=None,
        since=since,
        until=until,
    ).group_by(QueryLog.user_id)

    agg_by_user: dict[UUID | None, object] = {}
    deleted_rows = []
    for row in (await session.execute(window)).all():
        if row.user_id is None:
            # Deleted users: their rows keep only the email snapshot.
            # One bucket per snapshot would be nicer, but user_id is the
            # grouping key — split them here instead.
            deleted_rows.append(row)
        else:
            agg_by_user[row.user_id] = row

    # Deleted users grouped by snapshot email (separate pass — the SQL
    # above grouped them all under user_id NULL).
    deleted_items: list[UserUsageItem] = []
    if deleted_rows:
        per_snapshot = _apply_filters(
            select(
                QueryLog.user_email_snapshot,
                func.count(QueryLog.id).label("query_count"),
                func.sum(case((QueryLog.source == "mcp", 1), else_=0)).label("mcp"),
                func.sum(case((QueryLog.source == "rest", 1), else_=0)).label("rest"),
                func.sum(case((QueryLog.status == "error", 1), else_=0)).label(
                    "errors"
                ),
                func.sum(case((QueryLog.result_count == 0, 1), else_=0)).label(
                    "zeroes"
                ),
                func.coalesce(func.sum(QueryLog.tokens_input), 0).label("tok_in"),
                func.coalesce(func.sum(QueryLog.tokens_output), 0).label("tok_out"),
                func.coalesce(func.sum(QueryLog.cost_usd_micros), 0).label("cost"),
                func.max(QueryLog.created_at).label("last_at"),
            ),
            user_id=None,
            repository_id=None,
            tool_name=None,
            status=None,
            zero_results=None,
            q=None,
            since=since,
            until=until,
        ).where(QueryLog.user_id.is_(None)).group_by(QueryLog.user_email_snapshot)
        for row in (await session.execute(per_snapshot)).all():
            deleted_items.append(
                UserUsageItem(
                    user_id=None,
                    user_email=row.user_email_snapshot,
                    is_active=None,
                    is_deleted=True,
                    query_count=int(row.query_count),
                    mcp_count=int(row.mcp or 0),
                    rest_count=int(row.rest or 0),
                    error_count=int(row.errors or 0),
                    zero_result_count=int(row.zeroes or 0),
                    tokens_input=int(row.tok_in or 0),
                    tokens_output=int(row.tok_out or 0),
                    cost_usd_micros=int(row.cost or 0),
                    last_query_at=row.last_at,
                )
            )

    users = (
        await session.execute(select(User.id, User.email, User.is_active))
    ).all()

    items: list[UserUsageItem] = []
    for user_id, email, is_active in users:
        row = agg_by_user.get(user_id)
        items.append(
            UserUsageItem(
                user_id=user_id,
                user_email=email,
                is_active=bool(is_active),
                is_deleted=False,
                query_count=int(row.query_count) if row else 0,
                mcp_count=int(row.mcp or 0) if row else 0,
                rest_count=int(row.rest or 0) if row else 0,
                error_count=int(row.errors or 0) if row else 0,
                zero_result_count=int(row.zeroes or 0) if row else 0,
                tokens_input=int(row.tok_in or 0) if row else 0,
                tokens_output=int(row.tok_out or 0) if row else 0,
                cost_usd_micros=int(row.cost or 0) if row else 0,
                last_query_at=row.last_at if row else None,
            )
        )
    items.extend(deleted_items)
    items.sort(
        key=lambda item: (-item.query_count, (item.user_email or "").lower())
    )
    return UserUsageStats(
        items=items,
        total_users=len(users),
        active_users=sum(1 for item in items if item.query_count > 0),
    )


_BUCKET_SECONDS = {"hour": 3600, "day": 86400}
# Hard cap on returned buckets — 24h hourly is 24, 30d daily is 30; a
# misconstructed range must not turn into a megabyte of zeros.
_MAX_BUCKETS = 400


@router.get("/admin/query-logs/stats/timeseries", response_model=UsageTimeseries)
async def admin_query_logs_timeseries(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    bucket: str = Query(default="day", pattern="^(hour|day)$"),
    _admin: User = Depends(require_admin_or_owner),
    session: AsyncSession = Depends(get_db_session),
) -> UsageTimeseries:
    """Bucketed query counts + token/cost sums for the usage chart.

    Bucketing happens in Python, not SQL: date_trunc isn't portable to
    the sqlite test harness, and the retention sweep (default 30 days)
    bounds the table, so streaming a few thousand 6-column rows is
    cheaper than maintaining two SQL dialects. Buckets with no traffic
    are zero-filled so the chart shows gaps as gaps.
    """
    resolved_until = until or datetime.now(UTC)
    resolved_since = since or resolved_until - timedelta(days=30)
    if resolved_since >= resolved_until:
        raise HTTPException(status_code=422, detail="since must precede until")
    step = timedelta(seconds=_BUCKET_SECONDS[bucket])
    span_buckets = (
        int((resolved_until - resolved_since).total_seconds()) // int(
            step.total_seconds()
        )
        + 1
    )
    if span_buckets > _MAX_BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=f"range too wide for bucket={bucket} (max {_MAX_BUCKETS} buckets)",
        )

    rows = (
        await session.execute(
            _apply_filters(
                select(
                    QueryLog.created_at,
                    QueryLog.source,
                    QueryLog.status,
                    QueryLog.tokens_input,
                    QueryLog.tokens_output,
                    QueryLog.cost_usd_micros,
                ),
                user_id=None,
                repository_id=None,
                tool_name=None,
                status=None,
                zero_results=None,
                q=None,
                since=resolved_since,
                until=resolved_until,
            )
        )
    ).all()

    def _floor(ts: datetime) -> datetime:
        ts = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
        epoch = int(ts.timestamp())
        return datetime.fromtimestamp(
            epoch - epoch % int(step.total_seconds()), tz=UTC
        )

    by_bucket: dict[datetime, dict[str, int]] = {}
    start = _floor(resolved_since)
    cursor = start
    while cursor < resolved_until:
        by_bucket[cursor] = {
            "query_count": 0,
            "mcp_count": 0,
            "rest_count": 0,
            "error_count": 0,
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_usd_micros": 0,
        }
        cursor += step

    for created_at, source, status_value, tok_in, tok_out, cost in rows:
        slot = by_bucket.get(_floor(created_at))
        if slot is None:
            continue
        slot["query_count"] += 1
        if str(source) == "mcp":
            slot["mcp_count"] += 1
        else:
            slot["rest_count"] += 1
        if str(status_value) == "error":
            slot["error_count"] += 1
        slot["tokens_input"] += int(tok_in or 0)
        slot["tokens_output"] += int(tok_out or 0)
        slot["cost_usd_micros"] += int(cost or 0)

    return UsageTimeseries(
        bucket=bucket,
        since=resolved_since,
        until=resolved_until,
        items=[
            TimeseriesBucket(bucket_start=key, **values)
            for key, values in sorted(by_bucket.items())
        ],
    )


@router.get("/me/query-logs", response_model=QueryLogPage)
async def me_list_query_logs(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    repository_id: UUID | None = Query(default=None),
    tool_name: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern="^(ok|empty|error)$"),
    q: str | None = Query(default=None, max_length=200),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    current_user: User = Depends(require_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> QueryLogPage:
    return await _paginate(
        session,
        page=page,
        per_page=per_page,
        user_id=current_user.id,
        repository_id=repository_id,
        tool_name=tool_name,
        status=status,
        zero_results=None,
        q=q,
        since=since,
        until=until,
    )


class ForgetResponse(BaseModel):
    deleted: int = Field(ge=0)


@router.delete("/me/query-logs", response_model=ForgetResponse)
async def me_forget_query_logs(
    current_user: User = Depends(require_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ForgetResponse:
    """Drop *every* query_log row belonging to the caller.

    Privacy-side button — the user opts out of having their search
    history retained even before the daily retention sweep runs. Does
    NOT delete other users' rows; admins read query_logs as a
    separate channel (admin_list_query_logs above).
    """
    result = await session.execute(
        delete(QueryLog).where(QueryLog.user_id == current_user.id)
    )
    await session.commit()
    return ForgetResponse(deleted=int(result.rowcount or 0))
