"""Read-side tests: admin and /me query-log endpoints + forget endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.models.enums import (
    QueryLogSource,
    QueryLogStatus,
    UserRole,
)
from backend.app.models.query_log import QueryLog
from backend.app.models.user import User

_TEST_CSRF = "csrf-token"


async def _make_user(
    db_session,
    *,
    email: str,
    role: UserRole = UserRole.USER,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("password-1234"),
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


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


async def _seed_row(
    db_session,
    *,
    user_id,
    tool_name: str = "rest.retrieve",
    status: QueryLogStatus = QueryLogStatus.OK,
    result_count: int = 5,
    query_text: str = "auth",
    duration_ms: int = 100,
    created_at: datetime | None = None,
    user_email: str | None = "alice@example.com",
    repository_id=None,
    source: QueryLogSource = QueryLogSource.REST,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    cost_usd_micros: int | None = None,
) -> QueryLog:
    row = QueryLog(
        id=uuid4(),
        user_id=user_id,
        user_email_snapshot=user_email,
        source=source.value,
        tool_name=tool_name,
        repository_id=repository_id,
        query_text=query_text,
        query_truncated=False,
        top_k=10,
        result_count=result_count,
        duration_ms=duration_ms,
        status=status.value,
        created_at=created_at or datetime.now(UTC),
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd_micros=cost_usd_micros,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_admin_list_query_logs_requires_admin(client, db_session, settings):
    user = await _make_user(db_session, email="plain@example.com")
    await _authenticate(client, settings, user)
    response = await client.get("/api/admin/query-logs")
    assert response.status_code == 403


async def test_admin_list_query_logs_returns_rows(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")
    bob = await _make_user(db_session, email="bob@example.com")

    await _seed_row(
        db_session, user_id=alice.id, user_email="alice@example.com", query_text="alpha"
    )
    await _seed_row(
        db_session, user_id=bob.id, user_email="bob@example.com", query_text="beta"
    )
    await _seed_row(
        db_session, user_id=alice.id, user_email="alice@example.com", query_text="gamma"
    )

    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/query-logs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3

    # filter by user
    response = await client.get(f"/api/admin/query-logs?user_id={alice.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {row["query_text"] for row in body["items"]} == {"alpha", "gamma"}


async def test_admin_list_query_logs_zero_results_filter(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")

    await _seed_row(db_session, user_id=alice.id, result_count=5, query_text="hit")
    await _seed_row(db_session, user_id=alice.id, result_count=0, query_text="gap")

    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/query-logs?zero_results=true")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["query_text"] == "gap"


async def test_admin_query_logs_substring_filter(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")

    await _seed_row(db_session, user_id=alice.id, query_text="how does auth work")
    await _seed_row(db_session, user_id=alice.id, query_text="payment flow")
    await _seed_row(db_session, user_id=alice.id, query_text="AUTH middleware")

    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/query-logs?q=auth")
    body = response.json()
    assert body["total"] == 2
    assert {row["query_text"] for row in body["items"]} == {
        "how does auth work",
        "AUTH middleware",
    }


async def test_admin_query_logs_date_window(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")

    now = datetime.now(UTC)
    await _seed_row(
        db_session,
        user_id=alice.id,
        query_text="old",
        created_at=now - timedelta(days=10),
    )
    await _seed_row(
        db_session,
        user_id=alice.id,
        query_text="recent",
        created_at=now - timedelta(minutes=5),
    )

    since = (now - timedelta(days=1)).isoformat()
    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/query-logs", params={"since": since})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["query_text"] == "recent"


async def test_admin_query_logs_stats_basic(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")

    await _seed_row(db_session, user_id=alice.id, query_text="popular", duration_ms=10)
    await _seed_row(db_session, user_id=alice.id, query_text="popular", duration_ms=30)
    await _seed_row(db_session, user_id=alice.id, query_text="rare", duration_ms=200)
    await _seed_row(
        db_session,
        user_id=alice.id,
        query_text="empty",
        result_count=0,
        status=QueryLogStatus.EMPTY,
        duration_ms=50,
    )

    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/query-logs/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 4
    assert body["zero_result_count"] == 1
    assert body["error_count"] == 0
    assert body["p50_duration_ms"] is not None
    assert body["p95_duration_ms"] is not None
    top_q = {row["query_text"]: row["count"] for row in body["top_queries"]}
    assert top_q.get("popular") == 2
    assert top_q.get("rare") == 1


async def test_admin_user_stats_requires_admin(client, db_session, settings):
    user = await _make_user(db_session, email="plain@example.com")
    await _authenticate(client, settings, user)
    response = await client.get("/api/admin/query-logs/stats/users")
    assert response.status_code == 403


async def test_admin_user_stats_includes_silent_users(client, db_session, settings):
    # The whole point of the per-user view: bob ran nothing and must
    # still appear with a zero row — "who is NOT using cograph" is what
    # the operator opens the page for.
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")
    await _make_user(db_session, email="bob@example.com")

    await _seed_row(
        db_session,
        user_id=alice.id,
        user_email="alice@example.com",
        source=QueryLogSource.MCP,
        tokens_input=100,
        tokens_output=20,
        cost_usd_micros=150,
    )
    await _seed_row(
        db_session,
        user_id=alice.id,
        user_email="alice@example.com",
        source=QueryLogSource.REST,
        result_count=0,
        status=QueryLogStatus.EMPTY,
        tokens_input=50,
        tokens_output=10,
        cost_usd_micros=50,
    )

    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/query-logs/stats/users")
    assert response.status_code == 200
    body = response.json()
    # admin + alice + bob are current users; nobody is deleted.
    assert body["total_users"] == 3
    assert body["active_users"] == 1

    by_email = {item["user_email"]: item for item in body["items"]}
    assert set(by_email) == {
        "root@example.com",
        "alice@example.com",
        "bob@example.com",
    }

    alice_row = by_email["alice@example.com"]
    assert alice_row["query_count"] == 2
    assert alice_row["mcp_count"] == 1
    assert alice_row["rest_count"] == 1
    assert alice_row["zero_result_count"] == 1
    assert alice_row["tokens_input"] == 150
    assert alice_row["tokens_output"] == 30
    assert alice_row["cost_usd_micros"] == 200
    assert alice_row["last_query_at"] is not None
    assert alice_row["is_deleted"] is False

    bob_row = by_email["bob@example.com"]
    assert bob_row["query_count"] == 0
    assert bob_row["last_query_at"] is None
    assert bob_row["cost_usd_micros"] == 0

    # Most-active first, silent users after.
    assert body["items"][0]["user_email"] == "alice@example.com"


async def test_admin_user_stats_groups_deleted_users_by_snapshot(
    client, db_session, settings
):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    # Rows whose user was deleted: user_id is NULL, snapshot remains.
    await _seed_row(db_session, user_id=None, user_email="ghost@example.com")
    await _seed_row(db_session, user_id=None, user_email="ghost@example.com")

    await _authenticate(client, settings, admin)
    response = await client.get("/api/admin/query-logs/stats/users")
    body = response.json()

    ghost = next(i for i in body["items"] if i["user_email"] == "ghost@example.com")
    assert ghost["is_deleted"] is True
    assert ghost["user_id"] is None
    assert ghost["query_count"] == 2
    # Deleted users don't inflate the current-user denominator.
    assert body["total_users"] == 1


async def test_admin_user_stats_respects_since(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")
    now = datetime.now(UTC)
    await _seed_row(
        db_session, user_id=alice.id, created_at=now - timedelta(days=10)
    )
    await _seed_row(
        db_session, user_id=alice.id, created_at=now - timedelta(minutes=5)
    )

    await _authenticate(client, settings, admin)
    since = (now - timedelta(days=1)).isoformat()
    response = await client.get(
        "/api/admin/query-logs/stats/users", params={"since": since}
    )
    body = response.json()
    alice_row = next(
        i for i in body["items"] if i["user_email"] == "alice@example.com"
    )
    assert alice_row["query_count"] == 1


async def test_admin_timeseries_requires_admin(client, db_session, settings):
    user = await _make_user(db_session, email="plain@example.com")
    await _authenticate(client, settings, user)
    response = await client.get("/api/admin/query-logs/stats/timeseries")
    assert response.status_code == 403


async def test_admin_timeseries_buckets_and_zero_fill(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    alice = await _make_user(db_session, email="alice@example.com")
    now = datetime.now(UTC)

    # Two queries two days ago, one (mcp, error) today, nothing yesterday.
    await _seed_row(
        db_session,
        user_id=alice.id,
        created_at=now - timedelta(days=2),
        tokens_input=100,
        tokens_output=10,
        cost_usd_micros=300,
    )
    await _seed_row(
        db_session, user_id=alice.id, created_at=now - timedelta(days=2)
    )
    await _seed_row(
        db_session,
        user_id=alice.id,
        created_at=now,
        source=QueryLogSource.MCP,
        status=QueryLogStatus.ERROR,
        result_count=None,
    )

    await _authenticate(client, settings, admin)
    response = await client.get(
        "/api/admin/query-logs/stats/timeseries",
        params={"since": (now - timedelta(days=3)).isoformat(), "bucket": "day"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bucket"] == "day"
    # 3-day window → 3 or 4 day buckets depending on UTC-midnight
    # alignment; every bucket present, gaps zero-filled.
    assert 3 <= len(body["items"]) <= 4
    counts = [item["query_count"] for item in body["items"]]
    assert sum(counts) == 3
    assert 0 in counts  # the silent day is present, not skipped

    day_two = next(item for item in body["items"] if item["query_count"] == 2)
    assert day_two["rest_count"] == 2
    assert day_two["tokens_input"] == 100
    assert day_two["cost_usd_micros"] == 300

    day_now = next(item for item in body["items"] if item["query_count"] == 1)
    assert day_now["mcp_count"] == 1
    assert day_now["error_count"] == 1


async def test_admin_timeseries_rejects_oversized_range(client, db_session, settings):
    admin = await _make_user(db_session, email="root@example.com", role=UserRole.ADMIN)
    await _authenticate(client, settings, admin)
    now = datetime.now(UTC)
    response = await client.get(
        "/api/admin/query-logs/stats/timeseries",
        params={
            "since": (now - timedelta(days=30)).isoformat(),
            "bucket": "hour",
        },
    )
    assert response.status_code == 422


async def test_me_query_logs_returns_only_own(client, db_session, settings):
    alice = await _make_user(db_session, email="alice@example.com")
    bob = await _make_user(db_session, email="bob@example.com")

    await _seed_row(db_session, user_id=alice.id, query_text="alice-1")
    await _seed_row(db_session, user_id=alice.id, query_text="alice-2")
    await _seed_row(db_session, user_id=bob.id, query_text="bob-1")

    await _authenticate(client, settings, alice)
    response = await client.get("/api/me/query-logs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert all(row["user_email"] == "alice@example.com" for row in body["items"])


async def test_me_query_logs_requires_auth(client):
    response = await client.get("/api/me/query-logs")
    assert response.status_code in (401, 403)


async def test_me_forget_query_logs_drops_own_only(client, db_session, settings):
    alice = await _make_user(db_session, email="alice@example.com")
    bob = await _make_user(db_session, email="bob@example.com")
    await _seed_row(db_session, user_id=alice.id, query_text="alice-1")
    await _seed_row(db_session, user_id=alice.id, query_text="alice-2")
    await _seed_row(db_session, user_id=bob.id, query_text="bob-1")

    await _authenticate(client, settings, alice)
    response = await client.delete("/api/me/query-logs")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == 2

    remaining = (await db_session.scalars(select(QueryLog))).all()
    assert len(remaining) == 1
    assert remaining[0].user_id == bob.id
