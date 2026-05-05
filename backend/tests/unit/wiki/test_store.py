"""Tests for `WikiDocumentStore.upsert_pages` and `delete_orphan_pages`."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.document import Document
from backend.app.models.repository import Repository
from backend.app.wiki.schemas import (
    ReaderQuestion,
    ResolvedCitation,
    ResolvedPage,
    WikiPageQuality,
)
from backend.app.wiki.store import WikiDocumentStore

pytestmark = pytest.mark.asyncio


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-store",
        name="wiki-llm-store",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafef00d",
    )
    session.add(repo)
    await session.flush()
    return repo


def _page(
    *,
    slug: str,
    title: str,
    content: str,
    sort_order: int = 0,
    citations: list[ResolvedCitation] | None = None,
) -> ResolvedPage:
    return ResolvedPage(
        slug=slug,
        title=title,
        parent_slug=None,
        sort_order=sort_order,
        content=content,
        model="fake-v1",
        citations=citations or [],
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
    )


async def test_upsert_inserts_new_pages(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    pages = [
        _page(slug="index", title="Overview", content="# Overview", sort_order=0),
        _page(
            slug="architecture",
            title="Architecture",
            content="# Architecture",
            sort_order=1,
        ),
    ]
    persisted, skipped = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc123",
        plan_hash="planhash1",
        model="fake-v1",
        pages=pages,
    )
    assert len(persisted) == 2
    assert skipped == []

    rows = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert {r.slug for r in rows} == {"index", "architecture"}
    assert all(r.doc_type == "wiki" for r in rows)


async def test_upsert_skips_unchanged_content(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    page = _page(slug="index", title="Overview", content="# Same body")
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc123",
        plan_hash="planhash1",
        model="fake-v1",
        pages=[page],
    )

    persisted, skipped = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc124",  # commit changed but content identical
        plan_hash="planhash1",
        model="fake-v1",
        pages=[page],
    )
    assert skipped == ["index"]
    assert len(persisted) == 1
    # source_commit pointer was bumped despite the skip.
    row = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert row.source_commit == "abc124"


async def test_upsert_updates_changed_content(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc123",
        plan_hash="planhash1",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# Original")],
    )
    persisted, skipped = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc124",
        plan_hash="planhash2",
        model="fake-v2",
        pages=[
            _page(
                slug="index",
                title="Overview v2",
                content="# Updated",
                citations=[
                    ResolvedCitation(
                        id=str(uuid4()),
                        kind="node",
                        label="src.run",
                        file_path="src/main.py",
                    )
                ],
            )
        ],
    )
    assert skipped == []
    assert len(persisted) == 1

    row = (
        await db_session.execute(
            select(Document).where(Document.repository_id == repo.id)
        )
    ).scalar_one()
    assert row.title == "Overview v2"
    assert row.content == "# Updated"
    assert row.model == "fake-v2"
    assert row.source_commit == "abc124"
    assert len(row.citations) == 1


async def test_delete_orphan_pages_removes_missing_slugs(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="hash",
        model="fake-v1",
        pages=[
            _page(slug="index", title="Overview", content="# 1"),
            _page(slug="architecture", title="Arch", content="# 2"),
            _page(slug="deprecated", title="Old", content="# 3"),
        ],
    )

    deleted = await store.delete_orphan_pages(
        session=db_session,
        repository_id=repo.id,
        keep_slugs=["index", "architecture"],
    )
    assert deleted == 1

    remaining_slugs = {
        row.slug
        for row in (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    }
    assert remaining_slugs == {"index", "architecture"}


async def test_delete_orphan_pages_with_empty_keep_clears_all(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()

    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="hash",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# 1")],
    )
    deleted = await store.delete_orphan_pages(
        session=db_session,
        repository_id=repo.id,
        keep_slugs=[],
    )
    assert deleted == 1


async def test_upsert_does_not_touch_other_repos(db_session: AsyncSession) -> None:
    repo_a = await _make_repo(db_session)
    repo_b = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-store-b",
        name="b",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafe",
    )
    db_session.add(repo_b)
    await db_session.flush()

    store = WikiDocumentStore()
    await store.upsert_pages(
        session=db_session,
        repository_id=repo_a.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# A")],
    )
    await store.upsert_pages(
        session=db_session,
        repository_id=repo_b.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="fake-v1",
        pages=[_page(slug="index", title="Overview", content="# B")],
    )

    deleted = await store.delete_orphan_pages(
        session=db_session,
        repository_id=repo_a.id,
        keep_slugs=[],
    )
    assert deleted == 1

    remaining_b = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo_b.id)
            )
        )
        .scalars()
        .all()
    )
    assert {r.slug for r in remaining_b} == {"index"}


async def test_upsert_persists_quality_payload(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()
    quality = WikiPageQuality(
        code_node_citation_count=4,
        doc_chunk_citation_count=1,
        unresolved_count=2,
        low_confidence_chunk_count=3,
        covers_questions=[ReaderQuestion.HOW_TO_RUN, ReaderQuestion.PUBLIC_API],
        manifest_entries_used=5,
        has_diagram=True,
    )
    page = ResolvedPage(
        slug="index",
        title="Overview",
        parent_slug=None,
        sort_order=0,
        content="# Overview",
        model="fake-v1",
        citations=[],
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        unresolved_placeholders=[],
        quality=quality,
    )
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="planhash",
        model="fake-v1",
        pages=[page],
    )
    row = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    assert row.quality is not None
    assert row.quality["code_node_citation_count"] == 4
    assert row.quality["unresolved_count"] == 2
    assert row.quality["low_confidence_chunk_count"] == 3
    assert row.quality["covers_questions"] == ["how-to-run", "public-api"]
    assert row.quality["manifest_entries_used"] == 5
    assert row.quality["has_diagram"] is True

    # Re-running with updated content should refresh quality on the existing row
    quality_v2 = WikiPageQuality(
        code_node_citation_count=10,
        unresolved_count=0,
        has_diagram=False,
    )
    page_v2 = page.model_copy(
        update={"quality": quality_v2, "content": "# Overview v2"}
    )
    await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="planhash",
        model="fake-v1",
        pages=[page_v2],
    )
    refreshed = (
        (
            await db_session.execute(
                select(Document).where(Document.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    assert refreshed.quality is not None
    assert refreshed.quality["code_node_citation_count"] == 10
    assert refreshed.quality["unresolved_count"] == 0
    assert refreshed.quality["has_diagram"] is False


async def test_upsert_handles_empty_input(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)
    store = WikiDocumentStore()
    persisted, skipped = await store.upsert_pages(
        session=db_session,
        repository_id=repo.id,
        sync_run_id=None,
        source_commit="abc",
        plan_hash="h",
        model="m",
        pages=[],
    )
    assert persisted == []
    assert skipped == []
