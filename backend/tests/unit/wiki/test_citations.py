"""Tests for the citation resolver (`CitationResolver`)."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.wiki.citations import (
    UNRESOLVED_MARKER,
    CitationResolver,
    RepositorySlug,
)

pytestmark = pytest.mark.asyncio


def _slug(repo: Repository) -> RepositorySlug:
    return RepositorySlug(host=repo.host, owner=repo.owner, name=repo.name)


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-citations",
        name="wiki-llm-citations",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc123",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _add_node(
    session: AsyncSession, *, repo_id: UUID, qn: str, file_path: str = "src/pipeline.py"
) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path=file_path,
        qualified_name=qn,
        node_type=CodeNodeType.FUNCTION,
        name=qn.rsplit(".", 1)[-1],
        language="python",
        start_line=10,
        end_line=42,
        content="def fn(): pass\n",
        content_hash="c" * 64,
    )
    session.add(node)
    await session.flush()
    return node


async def _add_doc(
    session: AsyncSession,
    *,
    repo_id: UUID,
    file_path: str,
    title: str,
    chunks: list[tuple[int, list[str], str]],
) -> RepoDocument:
    doc = RepoDocument(
        repository_id=repo_id,
        file_path=file_path,
        title=title,
        content="\n\n".join(c[2] for c in chunks),
        content_hash="d" * 64,
        bytes=128,
    )
    session.add(doc)
    await session.flush()
    for index, heading_path, content in chunks:
        chunk = RepoDocumentChunk(
            document_id=doc.id,
            chunk_index=index,
            heading_path=heading_path,
            content=content,
            content_hash="d" * 64,
        )
        session.add(chunk)
    await session.flush()
    return doc


async def test_resolve_page_returns_input_when_no_placeholders(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    md, citations, unresolved = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown="# Plain page\n\nNo placeholders here.",
    )
    assert md == "# Plain page\n\nNo placeholders here."
    assert citations == []
    assert unresolved == []


async def test_resolve_page_resolves_node_placeholders(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="src.pipeline.run")

    md, citations, unresolved = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown=(
            "Entry point is [[node:src.pipeline.run]] which calls helpers.\n"
            "The same symbol appears again [[node:src.pipeline.run]]."
        ),
    )
    assert "[`src.pipeline.run`](/repos/" in md
    # Slug-form URL must match the FE route shape exactly — a UUID-form
    # `/repos/<uuid>/graph?…` falls through to the `*` catch-all and
    # renders NotFoundPage.
    expected_anchor = (
        f"/repos/example.com/test/wiki-llm-citations/graph?node={node.id}"
    )
    assert expected_anchor in md
    assert unresolved == []
    # Same target appears twice but is deduped in the citation list.
    assert len(citations) == 1
    assert citations[0].kind == "node"
    assert citations[0].id == str(node.id)
    assert citations[0].label == "run"


async def test_resolve_page_resolves_doc_placeholders(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_doc(
        db_session,
        repo_id=repo.id,
        file_path="docs/architecture.md",
        title="Architecture",
        chunks=[
            (0, ["Architecture", "Overview"], "The pipeline runs in 5 stages."),
            (1, ["Architecture", "Stages"], "Stage 1 is context."),
        ],
    )

    md, citations, unresolved = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown="See [[doc:docs/architecture.md#stages]] for the breakdown.",
    )
    # The FE docs route is keyed by slug from `repo_docs.slug.build_slug_map`
    # — `docs/architecture.md` slugifies to `architecture` (the `docs/`
    # prefix is stripped). The URL must hit that slug, not the raw path.
    assert "[Architecture](/repos/" in md
    assert "/docs/architecture#stages" in md
    assert "/docs/architecture.md" not in md
    assert unresolved == []

    assert len(citations) == 1
    assert citations[0].kind == "repo_doc_chunk"
    assert citations[0].file_path == "docs/architecture.md"


async def test_resolve_page_records_unresolved_placeholders(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)

    md, citations, unresolved = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown=("Missing [[node:does.not.exist]] and [[doc:gone.md#nowhere]]."),
    )
    # Unresolved placeholders render as a stable marker so the FE can style them.
    assert f"{UNRESOLVED_MARKER}node:does.not.exist" in md
    assert f"{UNRESOLVED_MARKER}doc:gone.md#nowhere" in md
    assert citations == []
    assert sorted(unresolved) == [
        "doc:gone.md#nowhere",
        "node:does.not.exist",
    ]


async def test_resolve_page_picks_first_doc_chunk_when_no_anchor(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_doc(
        db_session,
        repo_id=repo.id,
        file_path="docs/intro.md",
        title="Intro",
        chunks=[
            (0, ["Intro"], "The first chunk."),
            (1, ["Intro", "Why"], "The second chunk."),
        ],
    )

    md, citations, _ = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown="See [[doc:docs/intro.md]] for context.",
    )
    assert "[Intro](/repos/" in md
    assert len(citations) == 1
    assert citations[0].file_path == "docs/intro.md"


async def test_resolve_page_downgrades_non_markdown_doc_targets_to_prose(
    db_session: AsyncSession,
) -> None:
    """`repo_documents` indexes every text file in the checkout (.go,
    go.mod, …). The FE docs route only renders markdown, so a citation
    pointing at a non-markdown path is downgraded to bare path text in
    prose — no link, no warning marker."""
    repo = await _make_repo(db_session)
    await _add_doc(
        db_session,
        repo_id=repo.id,
        file_path="example/spec.go",
        title="Spec",
        chunks=[(0, ["Spec"], "package main")],
    )
    md, citations, unresolved = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown="See [[doc:example/spec.go]] for the definition.",
    )
    assert citations == []
    assert unresolved == []
    assert "[[doc:" not in md
    assert UNRESOLVED_MARKER not in md
    assert "example/spec.go" in md


async def test_resolve_page_downgrades_go_mod_to_prose(
    db_session: AsyncSession,
) -> None:
    """`go.mod` is indexed but is not markdown — must be downgraded to
    bare path, not produce a `⚠️ unresolved: doc:go.mod` chip."""
    repo = await _make_repo(db_session)
    md, citations, unresolved = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown="The module is declared in [[doc:go.mod]].",
    )
    assert citations == []
    assert unresolved == []
    assert UNRESOLVED_MARKER not in md
    assert "go.mod" in md


async def test_resolve_page_strips_backticks_around_placeholders(
    db_session: AsyncSession,
) -> None:
    """Regression: agents occasionally emit `` `[[node:Foo.Bar]]` `` (the
    placeholder wrapped in inline-code backticks). After resolution the
    outer backticks turned the link into ``​`[`Foo.Bar`](url)`​``,
    which renders as a chunk of inline code, not a link. The resolver
    must strip the surrounding backticks BEFORE substituting placeholders.
    """
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="src.pipeline.run")
    await _add_doc(
        db_session,
        repo_id=repo.id,
        file_path="docs/intro.md",
        title="Intro",
        chunks=[(0, ["Intro"], "Body.")],
    )

    md, citations, unresolved = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown=(
            "Call `[[node:src.pipeline.run]]` then read `[[doc:docs/intro.md]]`."
        ),
    )
    # No double-backtick wrap — the rendered links must be plain markdown
    # links, not inline code.
    assert "`[" not in md
    assert "[`src.pipeline.run`](/repos/" in md
    assert "[Intro](/repos/" in md
    assert unresolved == []
    assert {c.kind for c in citations} == {"node", "repo_doc_chunk"}


async def test_resolve_page_dedupes_repeated_node_citations(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="src.a.run")

    _, citations, _ = await CitationResolver().resolve_page(
        session=db_session,
        repo_slug=_slug(repo),
        repository_id=repo.id,
        markdown=(
            "[[node:src.a.run]] and again [[node:src.a.run]] "
            "and once more [[node:src.a.run]]."
        ),
    )
    assert len(citations) == 1
    assert citations[0].id == str(node.id)
    assert citations[0].kind == "node"


async def test_prevalidate_page_returns_only_unresolved_keys(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="src.pipeline.run")
    await _add_doc(
        db_session,
        repo_id=repo.id,
        file_path="docs/intro.md",
        title="Intro",
        chunks=[(0, ["Intro"], "The first chunk.")],
    )

    markdown = (
        "Known node [[node:src.pipeline.run]], "
        "missing node [[node:does.not.exist]], "
        "known doc [[doc:docs/intro.md]], "
        "missing doc [[doc:gone.md]]."
    )

    misses = await CitationResolver().prevalidate_page(
        session=db_session,
        repository_id=repo.id,
        markdown=markdown,
    )
    assert sorted(misses) == [
        "doc:gone.md",
        "node:does.not.exist",
    ]


async def test_prevalidate_page_no_placeholders_returns_empty(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    misses = await CitationResolver().prevalidate_page(
        session=db_session,
        repository_id=repo.id,
        markdown="# Plain page\n\nNo placeholders here.",
    )
    assert misses == []


async def test_prevalidate_page_dedupes_repeated_misses(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)

    misses = await CitationResolver().prevalidate_page(
        session=db_session,
        repository_id=repo.id,
        markdown=(
            "[[node:does.not.exist]] then [[node:does.not.exist]] "
            "then [[node:does.not.exist]]."
        ),
    )
    assert misses == ["node:does.not.exist"]
