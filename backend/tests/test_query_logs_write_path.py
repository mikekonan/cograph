"""Integration tests: REST/MCP entry points produce one query_log row each.

We patch `enqueue_query_log` to invoke the worker's `record_query_log`
synchronously instead of going through arq — same insert path, just
without the redis hop. This keeps the test deterministic.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token, hash_password
from backend.app.models.enums import QueryLogSource, QueryLogStatus, UserRole
from backend.app.models.query_log import QueryLog
from backend.app.models.user import User

_TEST_CSRF = "csrf-token"


async def _make_user(db_session, *, email: str = "alice@example.com") -> User:
    user = User(
        email=email,
        password_hash=hash_password("password-1234"),
        role=UserRole.USER,
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


def _wire_inline_query_log(monkeypatch, app) -> None:
    """Replace the arq-enqueue path with an inline call to record_query_log.

    `enqueue_query_log` is imported by two modules — REST and MCP. Patching
    both at the import site ensures the override survives FastAPI's
    closure capture in `Depends`-resolved handlers.
    """
    from backend.app.models.enums import QueryLogSource as _Src
    from backend.app.models.enums import QueryLogStatus as _Status
    from backend.app.pipeline.worker import record_query_log

    async def _inline_enqueue(**kwargs):
        # Mirror QueryLogPayload.to_kwargs() for the worker entry point.
        payload = {
            "user_id": str(kwargs["user_id"]) if kwargs.get("user_id") else None,
            "user_email_snapshot": kwargs.get("user_email"),
            "source": kwargs["source"].value
            if isinstance(kwargs["source"], _Src)
            else kwargs["source"],
            "tool_name": kwargs["tool_name"],
            "repository_id": str(kwargs["repository_id"])
            if kwargs.get("repository_id")
            else None,
            "collection_id": str(kwargs["collection_id"])
            if kwargs.get("collection_id")
            else None,
            "query_text": kwargs.get("query_text", ""),
            "query_truncated": False,
            "top_k": kwargs.get("top_k"),
            "result_count": kwargs.get("result_count"),
            "duration_ms": kwargs.get("duration_ms", 0),
            "status": kwargs["status"].value
            if isinstance(kwargs["status"], _Status)
            else kwargs["status"],
            "error_code": kwargs.get("error_code"),
            "client_label": kwargs.get("client_label"),
        }
        ctx = {"session_manager": app.state.session_manager}
        await record_query_log(ctx, payload)

    # Patch at every site where the symbol is bound. ``enqueue_query_log``
    # is bound into the closure of each instrumented handler at import
    # time, so patching ``backend.app.query_logs`` alone is not enough.
    monkeypatch.setattr("backend.app.api.retrieval.enqueue_query_log", _inline_enqueue)
    monkeypatch.setattr(
        "backend.app.api.md_collections.enqueue_query_log", _inline_enqueue
    )
    monkeypatch.setattr("backend.app.mcp.services.enqueue_query_log", _inline_enqueue)


async def test_post_retrieve_writes_one_query_log_row(
    client, db_session, settings, app, monkeypatch
):
    _wire_inline_query_log(monkeypatch, app)
    user = await _make_user(db_session, email="rest-retrieve@example.com")
    await _authenticate(client, settings, user)

    # We don't seed a real repository; this exercises the *log-write*
    # path on the error branch — `validate_retrieval_scope` raises
    # ApiError(422) inside `retrieve_composite`, and our try/finally
    # must still record the row with status=error.
    response = await client.post(
        "/api/retrieve",
        json={
            "query": "where is auth implemented",
            "top_k": 5,
            "include": {"chunks": True, "graph": False, "scores": False},
        },
    )
    assert response.status_code in (200, 422, 500, 503), (
        response.status_code,
        response.text,
    )

    rows = (
        await db_session.scalars(select(QueryLog).where(QueryLog.user_id == user.id))
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "rest.retrieve"
    assert row.source == QueryLogSource.REST.value
    assert row.query_text == "where is auth implemented"
    assert row.top_k == 5
    assert row.duration_ms >= 0
    assert row.user_email_snapshot == "rest-retrieve@example.com"
    # Status reflects the actual outcome — without a repo this is
    # an error path; we still expect a row.
    assert row.status in (
        QueryLogStatus.OK.value,
        QueryLogStatus.EMPTY.value,
        QueryLogStatus.ERROR.value,
    )


async def test_unauthenticated_retrieve_does_not_log(
    client, db_session, settings, app, monkeypatch
):
    """Anonymous traffic is out of scope for MVP — must not produce rows."""
    _wire_inline_query_log(monkeypatch, app)

    response = await client.post(
        "/api/retrieve",
        json={"query": "anon", "top_k": 5},
    )
    # Whatever status code — no row should appear.
    rows = (await db_session.scalars(select(QueryLog))).all()
    assert rows == [], f"expected no rows, got {len(rows)}"
    # Sanity: the request was made. /retrieve has no required-auth gate
    # (it's optional-user), so anonymous reaches the validator and
    # 422s on missing repository_id — that's fine.
    assert response.status_code in (200, 401, 403, 422, 500, 503)


async def test_retrieve_failure_records_error_status(
    client, db_session, settings, app, monkeypatch
):
    """If retrieve_composite raises, the row must persist as status=error.

    Starlette's `TestClient` propagates unhandled exceptions through
    the call stack by default (`raise_app_exceptions=True`), so we
    catch RuntimeError here instead of relying on a 5xx response
    code. The contract under test is that the `finally`-clause runs
    even when the body raises — and the row is written.
    """
    _wire_inline_query_log(monkeypatch, app)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("ProviderTimeout")

    monkeypatch.setattr("backend.app.api.retrieval.retrieve_composite", _boom)

    user = await _make_user(db_session, email="err@example.com")
    await _authenticate(client, settings, user)

    try:
        await client.post(
            "/api/retrieve",
            json={"query": "will fail", "top_k": 3},
        )
    except RuntimeError as exc:
        assert "ProviderTimeout" in str(exc)

    rows = (
        await db_session.scalars(select(QueryLog).where(QueryLog.user_id == user.id))
    ).all()
    assert len(rows) == 1
    assert rows[0].status == QueryLogStatus.ERROR.value
    assert rows[0].error_code == "RuntimeError"
