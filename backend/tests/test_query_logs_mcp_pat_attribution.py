"""MCP query logs: a PAT-authed call must attribute the row to the PAT owner.

Spec acceptance criterion #7 — when an external agent (Claude Desktop,
Cursor, etc.) calls an MCP tool with `Authorization: Bearer cgr_pat_…`,
the `query_logs.user_id` of the resulting row MUST equal the User who
owns the PAT, and `source` MUST be `mcp`. This is the only way the
admin "what is cograph being used for" page can attribute external
tool traffic back to a real person.

The MCP HTTP transport in tests is heavyweight (Streamable HTTP +
SSE + handshake), so this test pins the contract one layer below:

1. Construct an `AuthenticatedActor` the same way the
   `wrap_with_mcp_auth` ASGI wrapper does, by feeding a real PAT
   plaintext through `_resolve_pat` against the test DB.
2. Stash it on a fake MCP `ctx` whose `request_context.request.state`
   carries the actor — exactly the shape `current_user_from_context`
   walks.
3. Drive `mcp_query_log_scope` and assert the row's `user_id`,
   `user_email_snapshot`, `source`, and `client_label`.

If a future refactor breaks attribution (e.g. someone reads
`current_user.id` from the wrong contextvar), this test fails.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from sqlalchemy import select

from backend.app.core.auth import hash_password
from backend.app.core.deps import _resolve_pat
from backend.app.models.enums import QueryLogSource, QueryLogStatus, UserRole
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.query_log import QueryLog
from backend.app.models.user import User
from backend.app.mcp.services import mcp_query_log_scope


def _hash(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _make_user(db_session, *, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("password-1234"),
        role=UserRole.USER,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_pat(db_session, *, user: User, plaintext: str) -> PersonalAccessToken:
    pat = PersonalAccessToken(
        user_id=user.id,
        name="claude-desktop",
        token_hash=_hash(plaintext),
        token_prefix=plaintext[:16],
        scopes=["api:read", "mcp"],
    )
    db_session.add(pat)
    await db_session.commit()
    await db_session.refresh(pat)
    return pat


def _wire_inline_recorder(monkeypatch, app) -> None:
    """Same idea as `test_query_logs_privacy_flag` — intercept the arq
    pool to inline the worker call so a SELECT after the scope exits
    can see the row."""
    from backend.app.pipeline.worker import record_query_log
    from backend.app.query_logs import recorder

    async def _inline_get_pool(_app_state):
        class _Inline:
            async def enqueue_job(self, _name, payload):
                ctx = {"session_manager": app.state.session_manager}
                await record_query_log(ctx, payload)

        return _Inline()

    monkeypatch.setattr(recorder, "_get_pool", _inline_get_pool)


def _fake_mcp_ctx(app, actor) -> object:
    """Mirror the shape `current_user_from_context` walks: a `ctx` whose
    `.request_context.request.state.cograph_actor` is the actor, and
    whose `.request_context.request.app.state` is the FastAPI
    app.state (so the recorder can reach the arq pool).
    """
    request = SimpleNamespace(
        state=SimpleNamespace(cograph_actor=actor),
        app=SimpleNamespace(state=app.state),
        headers={"user-agent": "claude-desktop/1.2.3"},
    )
    request_context = SimpleNamespace(
        request=request,
        session=SimpleNamespace(client_info=SimpleNamespace(name="Claude Desktop")),
    )
    return SimpleNamespace(request_context=request_context)


async def test_mcp_pat_call_attributes_query_log_to_pat_owner(
    app, db_session, monkeypatch
) -> None:
    _wire_inline_recorder(monkeypatch, app)

    # Seed user + PAT with mcp scope.
    owner = await _make_user(db_session, email="pat-owner@example.com")
    plaintext = "cgr_pat_test_attribution_token"
    await _make_pat(db_session, user=owner, plaintext=plaintext)

    # Resolve the actor through the production resolver — same code
    # path the `wrap_with_mcp_auth` wrapper uses on every /mcp call.
    actor = await _resolve_pat(plaintext, db_session, client_ip="127.0.0.1")
    assert actor is not None, (
        "PAT must resolve — if this fails the rest of the test is meaningless"
    )
    assert actor.user.id == owner.id
    assert "mcp" in actor.scopes

    ctx = _fake_mcp_ctx(app, actor)

    async with mcp_query_log_scope(
        ctx=ctx,
        tool_name="cograph.retrieve",
        query_text="who owns this query",
        top_k=10,
    ) as log_bucket:
        log_bucket["result_count"] = 3

    rows = (
        await db_session.scalars(
            select(QueryLog).where(QueryLog.tool_name == "cograph.retrieve")
        )
    ).all()
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
    row = rows[0]
    assert row.user_id == owner.id, (
        "query_log must be attributed to the PAT owner — found "
        f"user_id={row.user_id!r}, expected {owner.id!r}"
    )
    assert row.user_email_snapshot == owner.email
    assert row.source == QueryLogSource.MCP.value
    assert row.status == QueryLogStatus.OK.value
    assert row.query_text == "who owns this query"
    assert row.top_k == 10
    assert row.result_count == 3
    # `_mcp_client_label` prefers session.client_info.name over the UA.
    assert row.client_label == "Claude Desktop"


async def test_mcp_anonymous_ctx_writes_no_row(app, db_session, monkeypatch) -> None:
    """Sanity: when the ctx has no actor (no PAT), no row is written.

    The recorder's gate is `current_user_from_context(ctx) is not
    None` — we exercise the negative branch here so a future refactor
    can't silently start logging anonymous MCP traffic into the
    user-attribution table.
    """
    _wire_inline_recorder(monkeypatch, app)

    # ctx with no `cograph_actor`.
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=app.state),
        headers={"user-agent": "anonymous/1.0"},
    )
    request_context = SimpleNamespace(request=request, session=None)
    ctx = SimpleNamespace(request_context=request_context)

    async with mcp_query_log_scope(
        ctx=ctx,
        tool_name="cograph.retrieve",
        query_text="anonymous",
        top_k=5,
    ) as log_bucket:
        log_bucket["result_count"] = 0

    rows = (await db_session.scalars(select(QueryLog))).all()
    assert rows == [], f"expected no rows, got {len(rows)}"
