from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.md_rag.job_tracker import MdJobTracker
from backend.app.models.enums import MdJobKind, MdJobStatus
from backend.app.models.md_collection import MdCollection


@pytest.fixture
async def collection(db_session: AsyncSession) -> MdCollection:
    col = MdCollection(name="test-col", description="", visibility="private")
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)
    return col


async def test_create_job(db_session: AsyncSession, collection: MdCollection) -> None:
    job = await MdJobTracker.create(
        db_session, collection_id=collection.id, kind=MdJobKind.EMBED
    )
    assert job.collection_id == collection.id
    assert job.kind == MdJobKind.EMBED
    assert job.status == MdJobStatus.QUEUED
    assert job.result_summary == {}
    assert job.started_at is None
    assert job.finished_at is None


async def test_update_status_to_running_sets_started_at(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    job = await MdJobTracker.create(
        db_session, collection_id=collection.id, kind=MdJobKind.EMBED
    )
    updated = await MdJobTracker.update_status(
        db_session, job_id=job.id, status=MdJobStatus.RUNNING
    )
    assert updated is not None
    assert updated.status == MdJobStatus.RUNNING
    assert updated.started_at is not None
    assert updated.finished_at is None


async def test_update_status_to_success_sets_finished_at(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    job = await MdJobTracker.create(
        db_session, collection_id=collection.id, kind=MdJobKind.EMBED
    )
    await MdJobTracker.update_status(
        db_session, job_id=job.id, status=MdJobStatus.RUNNING
    )
    updated = await MdJobTracker.update_status(
        db_session,
        job_id=job.id,
        status=MdJobStatus.SUCCESS,
        result_summary={"embedded_nodes": 5},
    )
    assert updated is not None
    assert updated.status == MdJobStatus.SUCCESS
    assert updated.finished_at is not None
    assert updated.result_summary == {"embedded_nodes": 5}


async def test_update_status_to_error_sets_finished_at(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    job = await MdJobTracker.create(
        db_session, collection_id=collection.id, kind=MdJobKind.RESOLVE_LINKS
    )
    updated = await MdJobTracker.update_status(
        db_session,
        job_id=job.id,
        status=MdJobStatus.ERROR,
        error_message="boom",
    )
    assert updated is not None
    assert updated.status == MdJobStatus.ERROR
    assert updated.finished_at is not None
    assert updated.error_message == "boom"


async def test_update_progress(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    job = await MdJobTracker.create(
        db_session, collection_id=collection.id, kind=MdJobKind.EMBED
    )
    updated = await MdJobTracker.update_progress(
        db_session, job_id=job.id, processed=10, total=100
    )
    assert updated is not None
    assert updated.result_summary["processed"] == 10
    assert updated.result_summary["total"] == 100


async def test_list_for_collection(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    for kind in (MdJobKind.EMBED, MdJobKind.RESOLVE_LINKS):
        await MdJobTracker.create(db_session, collection_id=collection.id, kind=kind)
    jobs = await MdJobTracker.list_for_collection(
        db_session, collection_id=collection.id, limit=10
    )
    assert len(jobs) == 2
    assert jobs[0].created_at >= jobs[1].created_at


async def test_get_latest_by_kind(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    job1 = await MdJobTracker.create(
        db_session, collection_id=collection.id, kind=MdJobKind.EMBED
    )
    job2 = await MdJobTracker.create(
        db_session, collection_id=collection.id, kind=MdJobKind.EMBED
    )
    found = await MdJobTracker.get_latest_by_kind(
        db_session, collection_id=collection.id, kind=MdJobKind.EMBED
    )
    assert found is not None
    assert found.id in (job1.id, job2.id)


async def test_update_missing_job_returns_none(
    db_session: AsyncSession,
) -> None:
    from uuid import uuid4

    result = await MdJobTracker.update_status(
        db_session, job_id=uuid4(), status=MdJobStatus.RUNNING
    )
    assert result is None
