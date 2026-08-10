"""Retention cron: `prune_query_logs` drops rows older than the cutoff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from backend.app.config import Settings
from backend.app.models.enums import QueryLogSource, QueryLogStatus
from backend.app.models.query_log import QueryLog
from backend.app.pipeline.worker import prune_query_logs


async def _seed_row(
    db_session,
    *,
    age_days: int,
    query_text: str,
) -> QueryLog:
    row = QueryLog(
        id=uuid4(),
        source=QueryLogSource.REST.value,
        tool_name="rest.retrieve",
        query_text=query_text,
        query_truncated=False,
        duration_ms=10,
        status=QueryLogStatus.OK.value,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_prune_query_logs_drops_only_old_rows(
    app, db_session, settings: Settings
):
    await _seed_row(db_session, age_days=1, query_text="fresh")
    await _seed_row(db_session, age_days=29, query_text="just-in-window")
    await _seed_row(db_session, age_days=31, query_text="just-out-of-window")
    await _seed_row(db_session, age_days=365, query_text="ancient")

    # Use app.state.session_manager so the prune job runs against the
    # same engine that fed the seeded rows.
    ctx = {
        "settings": settings,
        "session_manager": app.state.session_manager,
    }
    # `query_log.retention_days` defaults to 30, so days 31 and 365 must
    # drop, days 1 and 29 must stay.
    result = await prune_query_logs(ctx)
    assert result["rows_deleted"] == 2

    surviving = (
        await db_session.scalars(select(QueryLog).order_by(QueryLog.query_text))
    ).all()
    assert [r.query_text for r in surviving] == ["fresh", "just-in-window"]


async def test_prune_query_logs_no_op_on_empty_table(app, settings: Settings) -> None:
    ctx = {
        "settings": settings,
        "session_manager": app.state.session_manager,
    }
    result = await prune_query_logs(ctx)
    assert result["rows_deleted"] == 0


async def test_record_query_log_inserts_one_row(
    app, db_session, settings: Settings
) -> None:
    """The worker task itself — a smoke test independent of the wiring."""
    from backend.app.pipeline.worker import record_query_log

    payload = {
        "user_id": None,
        "user_email_snapshot": "alice@example.com",
        "source": "rest",
        "tool_name": "rest.retrieve",
        "repository_id": None,
        "collection_id": None,
        "query_text": "hello",
        "query_truncated": False,
        "top_k": 10,
        "result_count": 5,
        "duration_ms": 42,
        "status": "ok",
        "error_code": None,
        "client_label": "test-agent",
    }
    ctx = {"session_manager": app.state.session_manager}
    result = await record_query_log(ctx, payload)
    assert result == {"recorded": 1}

    rows = (await db_session.scalars(select(QueryLog))).all()
    assert len(rows) == 1
    assert rows[0].tool_name == "rest.retrieve"
    assert rows[0].user_email_snapshot == "alice@example.com"
    assert rows[0].client_label == "test-agent"
