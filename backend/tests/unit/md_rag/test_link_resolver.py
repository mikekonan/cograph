from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.md_rag.link_resolver import MdLinkResolver
from backend.app.models.md_collection import MdCollection, MdDocument, MdLink


@pytest.fixture
async def collection(db_session: AsyncSession) -> MdCollection:
    col = MdCollection(name="test-col", description="", visibility="private")
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)
    return col


async def test_resolve_exact_source_key(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    doc_a = MdDocument(
        collection_id=collection.id,
        source_key="docs/a.md",
        title="A",
        content="link to b",
        content_hash="abc123",
        bytes=10,
        word_count=2,
        line_count=1,
    )
    doc_b = MdDocument(
        collection_id=collection.id,
        source_key="docs/b.md",
        title="B",
        content="body",
        content_hash="def456",
        bytes=4,
        word_count=1,
        line_count=1,
    )
    db_session.add_all([doc_a, doc_b])
    await db_session.commit()
    await db_session.refresh(doc_a)
    await db_session.refresh(doc_b)

    link = MdLink(
        source_document_id=doc_a.id,
        href="docs/b.md",
        link_type="markdown",
    )
    db_session.add(link)
    await db_session.commit()

    resolver = MdLinkResolver()
    resolved = await resolver.resolve_collection(
        session=db_session, collection_id=collection.id
    )
    assert resolved == 1

    await db_session.refresh(link)
    assert link.target_document_id == doc_b.id


async def test_resolve_basename(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    doc_a = MdDocument(
        collection_id=collection.id,
        source_key="a.md",
        title="A",
        content="link",
        content_hash="abc123",
        bytes=4,
        word_count=1,
        line_count=1,
    )
    doc_b = MdDocument(
        collection_id=collection.id,
        source_key="sub/b.md",
        title="B",
        content="body",
        content_hash="def456",
        bytes=4,
        word_count=1,
        line_count=1,
    )
    db_session.add_all([doc_a, doc_b])
    await db_session.commit()
    await db_session.refresh(doc_a)
    await db_session.refresh(doc_b)

    link = MdLink(
        source_document_id=doc_a.id,
        href="b.md",
        link_type="markdown",
    )
    db_session.add(link)
    await db_session.commit()

    resolver = MdLinkResolver()
    resolved = await resolver.resolve_collection(
        session=db_session, collection_id=collection.id
    )
    assert resolved == 1
    await db_session.refresh(link)
    assert link.target_document_id == doc_b.id


async def test_resolve_wiki_name_without_ext(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    doc_a = MdDocument(
        collection_id=collection.id,
        source_key="a.md",
        title="A",
        content="link",
        content_hash="abc123",
        bytes=4,
        word_count=1,
        line_count=1,
    )
    doc_b = MdDocument(
        collection_id=collection.id,
        source_key="b.md",
        title="B",
        content="body",
        content_hash="def456",
        bytes=4,
        word_count=1,
        line_count=1,
    )
    db_session.add_all([doc_a, doc_b])
    await db_session.commit()
    await db_session.refresh(doc_a)
    await db_session.refresh(doc_b)

    link = MdLink(
        source_document_id=doc_a.id,
        href="b",
        link_type="wiki",
    )
    db_session.add(link)
    await db_session.commit()

    resolver = MdLinkResolver()
    resolved = await resolver.resolve_collection(
        session=db_session, collection_id=collection.id
    )
    assert resolved == 1
    await db_session.refresh(link)
    assert link.target_document_id == doc_b.id


async def test_resolve_no_match_leaves_null(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    doc = MdDocument(
        collection_id=collection.id,
        source_key="a.md",
        title="A",
        content="link",
        content_hash="abc123",
        bytes=4,
        word_count=1,
        line_count=1,
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    link = MdLink(
        source_document_id=doc.id,
        href="missing.md",
        link_type="markdown",
    )
    db_session.add(link)
    await db_session.commit()

    resolver = MdLinkResolver()
    resolved = await resolver.resolve_collection(
        session=db_session, collection_id=collection.id
    )
    assert resolved == 0
    await db_session.refresh(link)
    assert link.target_document_id is None


async def test_resolve_with_progress_callback(
    db_session: AsyncSession, collection: MdCollection
) -> None:
    doc = MdDocument(
        collection_id=collection.id,
        source_key="a.md",
        title="A",
        content="link",
        content_hash="abc123",
        bytes=4,
        word_count=1,
        line_count=1,
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    for i in range(55):
        db_session.add(
            MdLink(
                source_document_id=doc.id,
                href=f"missing-{i}.md",
                link_type="markdown",
            )
        )
    await db_session.commit()

    progress_calls: list[tuple[int, int, str | None]] = []

    async def cb(processed: int, total: int, current_item: str | None = None) -> None:
        progress_calls.append((processed, total, current_item))

    resolver = MdLinkResolver()
    await resolver.resolve_collection(
        session=db_session, collection_id=collection.id, progress_callback=cb
    )
    assert len(progress_calls) == 2  # 50 and 55
    assert progress_calls[-1][:2] == (55, 55)
