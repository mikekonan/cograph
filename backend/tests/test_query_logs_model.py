"""Schema-level tests for `QueryLog` — round-trip, defaults, truncation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from backend.app.models.enums import QueryLogSource, QueryLogStatus
from backend.app.models.query_log import QueryLog
from backend.app.query_logs import truncate_query_text


async def test_query_log_round_trip_minimal(db_session) -> None:
    row = QueryLog(
        user_id=None,
        user_email_snapshot=None,
        source=QueryLogSource.REST.value,
        tool_name="rest.retrieve",
        query_text="hello world",
        query_truncated=False,
        top_k=10,
        result_count=3,
        duration_ms=42,
        status=QueryLogStatus.OK.value,
        created_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()

    fetched = (await db_session.scalars(select(QueryLog))).all()
    assert len(fetched) == 1
    persisted = fetched[0]
    assert persisted.tool_name == "rest.retrieve"
    assert persisted.source == QueryLogSource.REST.value
    assert persisted.status == QueryLogStatus.OK.value
    assert persisted.query_text == "hello world"
    assert persisted.query_truncated is False
    assert persisted.top_k == 10
    assert persisted.result_count == 3
    assert persisted.duration_ms == 42
    assert persisted.id is not None


async def test_truncate_query_text_caps_to_max_bytes() -> None:
    text, truncated = truncate_query_text("a" * 500, max_bytes=200)
    assert len(text) == 200
    assert truncated is True

    # ASCII below cap: unchanged.
    text, truncated = truncate_query_text("short", max_bytes=200)
    assert text == "short"
    assert truncated is False


async def test_truncate_query_text_respects_utf8_boundaries() -> None:
    # 4-byte emoji exceeds the cap; the truncator must not split a code point.
    emoji_text = "🚀" * 100  # 400 bytes
    text, truncated = truncate_query_text(emoji_text, max_bytes=10)
    # Must decode cleanly — would raise on partial codepoint.
    text.encode("utf-8").decode("utf-8")
    assert truncated is True
    # 10 bytes fits exactly 2 emojis (4 bytes each = 8 bytes), 3rd would overflow.
    assert len(text) <= 2


async def test_truncate_query_text_handles_none_and_empty() -> None:
    assert truncate_query_text("", max_bytes=10) == ("", False)
    assert truncate_query_text(None, max_bytes=10) == ("", False)


async def test_query_log_stores_truncated_flag(db_session) -> None:
    long_text = "x" * 500
    text, was_truncated = truncate_query_text(long_text, max_bytes=200)

    row = QueryLog(
        id=uuid4(),
        source=QueryLogSource.MCP.value,
        tool_name="cograph.retrieve",
        query_text=text,
        query_truncated=was_truncated,
        duration_ms=100,
        status=QueryLogStatus.OK.value,
        created_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()

    fetched = (await db_session.scalars(select(QueryLog))).one()
    assert fetched.query_truncated is True
    assert len(fetched.query_text) == 200


async def test_query_log_status_enum_values() -> None:
    # All three statuses we shipped must persist as their string values.
    assert QueryLogStatus.OK.value == "ok"
    assert QueryLogStatus.EMPTY.value == "empty"
    assert QueryLogStatus.ERROR.value == "error"


async def test_query_log_source_enum_values() -> None:
    assert QueryLogSource.REST.value == "rest"
    assert QueryLogSource.MCP.value == "mcp"
