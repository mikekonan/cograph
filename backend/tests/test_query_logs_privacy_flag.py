"""Repository-level privacy opt-out: `repositories.log_queries = false`.

The flag is read by the async recorder in `backend/app/query_logs/
recorder.py` through a short-lived in-memory cache. These tests pin
the contract end-to-end via the recorder so a future refactor can't
silently break the privacy guarantee.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.app.models.enums import (
    QueryLogSource,
    QueryLogStatus,
    RepositoryStatus,
    RepositoryVisibility,
    SyncSchedule,
)
from backend.app.models.query_log import QueryLog
from backend.app.models.repository import Repository
from backend.app.query_logs.recorder import (
    enqueue_query_log,
    invalidate_repo_log_flag_cache,
)


async def _seed_repository(db_session, *, log_queries: bool) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="git@example.com:o/r.git",
        name=f"r-{log_queries}",
        owner="o",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.PUBLIC,
        sync_schedule=SyncSchedule.MANUAL,
        log_queries=log_queries,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return repo


def _wire_inline_recorder(monkeypatch, app) -> None:
    """Route `enqueue_query_log`'s eventual arq insert through an inline
    `record_query_log` so the test can SELECT immediately. We only
    intercept the LAST hop (the arq pool) — the recorder's gate (the
    flag-cache + `_is_logging_allowed` check) still runs unmodified.
    This is the whole point: we are testing that the gate STOPS the
    pool call in the first place.
    """
    from backend.app.pipeline.worker import record_query_log
    from backend.app.query_logs import recorder

    async def _inline_get_pool(_app_state):
        class _Inline:
            async def enqueue_job(self, _name, payload):
                ctx = {"session_manager": app.state.session_manager}
                await record_query_log(ctx, payload)

        return _Inline()

    monkeypatch.setattr(recorder, "_get_pool", _inline_get_pool)


async def test_log_queries_false_skips_write(app, db_session, monkeypatch) -> None:
    """When a repo has `log_queries = false`, the recorder must NOT
    write a row even if all other conditions are met."""
    _wire_inline_recorder(monkeypatch, app)
    repo = await _seed_repository(db_session, log_queries=False)

    await enqueue_query_log(
        app_state=app.state,
        user_id=None,
        user_email="nobody@example.com",
        source=QueryLogSource.REST,
        tool_name="rest.retrieve",
        query_text="should be dropped",
        repository_id=repo.id,
        top_k=10,
        result_count=1,
        duration_ms=12,
        status=QueryLogStatus.OK,
    )

    rows = (await db_session.scalars(select(QueryLog))).all()
    assert rows == [], (
        "expected zero query_log rows for opted-out repo, "
        f"got {[r.tool_name for r in rows]}"
    )


async def test_log_queries_true_records_row(app, db_session, monkeypatch) -> None:
    """Sanity: when the flag is True the recorder writes as before."""
    _wire_inline_recorder(monkeypatch, app)
    repo = await _seed_repository(db_session, log_queries=True)

    await enqueue_query_log(
        app_state=app.state,
        user_id=None,
        user_email="alice@example.com",
        source=QueryLogSource.REST,
        tool_name="rest.retrieve",
        query_text="should land",
        repository_id=repo.id,
        top_k=10,
        result_count=2,
        duration_ms=8,
        status=QueryLogStatus.OK,
    )

    rows = (await db_session.scalars(select(QueryLog))).all()
    assert len(rows) == 1
    assert rows[0].repository_id == repo.id
    assert rows[0].query_text == "should land"


async def test_flag_change_busts_cache(app, db_session, monkeypatch) -> None:
    """A flag flip plus an explicit cache invalidation must affect the
    very next recorder call — operators rely on this to stop logging
    immediately, without waiting for the TTL window."""
    _wire_inline_recorder(monkeypatch, app)
    repo = await _seed_repository(db_session, log_queries=True)

    # 1st call — flag is True, row lands, decision now cached.
    await enqueue_query_log(
        app_state=app.state,
        user_id=None,
        user_email="alice@example.com",
        source=QueryLogSource.REST,
        tool_name="rest.retrieve",
        query_text="first",
        repository_id=repo.id,
        top_k=10,
        result_count=1,
        duration_ms=5,
        status=QueryLogStatus.OK,
    )

    # Flip the column and invalidate the recorder's in-process cache,
    # which is what the PATCH endpoint does on `log_queries` change.
    repo.log_queries = False
    await db_session.commit()
    invalidate_repo_log_flag_cache(app_state=app.state, repository_id=repo.id)

    # 2nd call — must be skipped.
    await enqueue_query_log(
        app_state=app.state,
        user_id=None,
        user_email="alice@example.com",
        source=QueryLogSource.REST,
        tool_name="rest.retrieve",
        query_text="second-should-be-skipped",
        repository_id=repo.id,
        top_k=10,
        result_count=1,
        duration_ms=5,
        status=QueryLogStatus.OK,
    )

    rows = (
        await db_session.scalars(select(QueryLog).order_by(QueryLog.query_text))
    ).all()
    assert [r.query_text for r in rows] == ["first"], (
        "second call must be skipped after flag flip + cache bust; "
        f"got {[r.query_text for r in rows]}"
    )


async def test_repo_id_absent_bypasses_flag_check(app, db_session, monkeypatch) -> None:
    """When `repository_id` is None there is no privacy decision to
    make — the recorder must always write (covers MD-collection
    searches and any future repo-less query path)."""
    _wire_inline_recorder(monkeypatch, app)

    await enqueue_query_log(
        app_state=app.state,
        user_id=None,
        user_email="nobody@example.com",
        source=QueryLogSource.REST,
        tool_name="rest.md_collection_search",
        query_text="repo-less query",
        repository_id=None,
        collection_id=None,
        top_k=10,
        result_count=0,
        duration_ms=1,
        status=QueryLogStatus.EMPTY,
    )

    rows = (await db_session.scalars(select(QueryLog))).all()
    assert len(rows) == 1
    assert rows[0].repository_id is None
