"""Tests for `citation_repair.repair_markdown` and the DB shell."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.document import Document
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.wiki.citation_repair import (
    RepairResult,
    repair_markdown,
    repair_page_citations,
)
from backend.app.wiki.citations import RepositorySlug

def _slug() -> RepositorySlug:
    return RepositorySlug(host="example.com", owner="acme", name="widget")


def test_repair_markdown_upgrades_uuid_form_url_to_slug_form() -> None:
    repo_id = UUID("ae5c6624-90f3-4fbd-ae96-525b84f61b84")
    node_id = UUID("019de76b-191c-7f70-825c-97abd7684777")
    content = (
        "MerchantID is exposed as `domain.MerchantID` "
        f"[`domain.MerchantID`](/repos/{repo_id}/graph?node={node_id}).\n"
    )

    result = repair_markdown(
        content=content,
        citations=[
            {
                "id": str(node_id),
                "kind": "node",
                "label": "MerchantID",
                "file_path": "domain/merchant.go",
            }
        ],
        repository_id=repo_id,
        repo_slug=_slug(),
        existing_node_ids={node_id},
        qn_to_node_id={"domain.MerchantID": node_id},
    )

    assert result.url_format_upgraded == 1
    assert result.patched == 0
    assert result.dropped == 0
    assert (
        f"/repos/example.com/acme/widget/graph?node={node_id}"
        in result.new_content
    )
    assert f"/repos/{repo_id}/graph?node=" not in result.new_content
    assert result.new_citations[0]["id"] == str(node_id)


def test_repair_markdown_rewrites_stale_uuid_via_qualified_name() -> None:
    repo_id = uuid4()
    old_id = uuid4()
    new_id = uuid4()
    content = (
        f"[`pkg.OldName`](/repos/example.com/acme/widget/graph?node={old_id}) "
        "is the renamed export.\n"
    )

    result = repair_markdown(
        content=content,
        citations=[
            {
                "id": str(old_id),
                "kind": "node",
                "label": "OldName",
                "file_path": "pkg/old.go",
            }
        ],
        repository_id=repo_id,
        repo_slug=_slug(),
        existing_node_ids=set(),  # stale
        qn_to_node_id={"pkg.OldName": new_id},
    )

    assert result.patched == 1
    assert result.dropped == 0
    assert f"node={new_id}" in result.new_content
    assert f"node={old_id}" not in result.new_content
    assert result.new_citations[0]["id"] == str(new_id)


def test_repair_markdown_drops_link_when_qualified_name_also_gone() -> None:
    repo_id = uuid4()
    old_id = uuid4()
    content = (
        f"[`pkg.Removed`](/repos/example.com/acme/widget/graph?node={old_id}) "
        "is gone.\n"
    )

    result = repair_markdown(
        content=content,
        citations=[
            {
                "id": str(old_id),
                "kind": "node",
                "label": "Removed",
                "file_path": "pkg/removed.go",
            }
        ],
        repository_id=repo_id,
        repo_slug=_slug(),
        existing_node_ids=set(),
        qn_to_node_id={},  # genuinely deleted
    )

    assert result.patched == 0
    assert result.dropped == 1
    # Link is reduced to bare backticked label — the prose still reads
    # naturally without a dead-link href.
    assert "[`pkg.Removed`]" not in result.new_content
    assert "`pkg.Removed`" in result.new_content
    assert result.new_citations == []


def test_repair_markdown_leaves_current_citations_untouched() -> None:
    repo_id = uuid4()
    node_id = uuid4()
    content = (
        f"[`pkg.Active`](/repos/example.com/acme/widget/graph?node={node_id}) "
        "still exists.\n"
    )

    result = repair_markdown(
        content=content,
        citations=[
            {
                "id": str(node_id),
                "kind": "node",
                "label": "Active",
                "file_path": "pkg/active.go",
            }
        ],
        repository_id=repo_id,
        repo_slug=_slug(),
        existing_node_ids={node_id},
        qn_to_node_id={"pkg.Active": node_id},
    )

    assert result.patched == 0
    assert result.dropped == 0
    assert result.unchanged == 1
    assert result.url_format_upgraded == 0
    assert result.new_content == content
    assert result.changed is False


def test_repair_markdown_upgrades_doc_links_too() -> None:
    repo_id = uuid4()
    content = (
        f"See [Architecture](/repos/{repo_id}/docs/architecture) for the "
        "subsystem overview.\n"
    )

    result = repair_markdown(
        content=content,
        citations=[],
        repository_id=repo_id,
        repo_slug=_slug(),
        existing_node_ids=set(),
        qn_to_node_id={},
    )

    assert result.url_format_upgraded == 1
    assert "/repos/example.com/acme/widget/docs/architecture" in result.new_content


def test_repair_markdown_skips_other_repo_links() -> None:
    """A href naming a different repository's UUID is suspicious — the
    citation was already wrong before any migration. Don't rewrite it
    to point at the current repo, which would be misleading."""
    repo_id = uuid4()
    other_repo = uuid4()
    other_node = uuid4()
    content = (
        f"[`other.Symbol`](/repos/{other_repo}/graph?node={other_node}) "
        "is in a different repo.\n"
    )

    result = repair_markdown(
        content=content,
        citations=[],
        repository_id=repo_id,
        repo_slug=_slug(),
        existing_node_ids=set(),
        qn_to_node_id={},
    )
    assert result.url_format_upgraded == 0
    assert result.new_content == content


# ---------------------------------------------------------------------------
# DB-shell tests for `repair_page_citations`
# ---------------------------------------------------------------------------


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/acme/widget",
        name="widget",
        owner="acme",
        branch="main",
        status="ready",
        sync_schedule="manual",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _add_node(
    session: AsyncSession,
    *,
    repo_id: UUID,
    qn: str,
    file_path: str = "src/pipeline.py",
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


async def _add_wiki_page(
    session: AsyncSession,
    *,
    repo_id: UUID,
    slug: str,
    content: str,
    citations: list[dict[str, object]],
) -> Document:
    doc = Document(
        repository_id=repo_id,
        slug=slug,
        title="A page",
        doc_type="wiki",
        sort_order=0,
        content=content,
        content_hash="h" * 64,
        source_hash="s" * 64,
        model="wiki-llm-v1",
        source_node_ids=[],
        source_repo_doc_chunk_ids=[],
        citations=citations,
    )
    session.add(doc)
    await session.flush()
    return doc


@pytest.mark.asyncio
async def test_repair_page_citations_persists_url_upgrade(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="pkg.Live")
    repo_id = repo.id
    repo_host, repo_owner, repo_name = repo.host, repo.owner, repo.name
    node_id = node.id
    content = (
        f"[`pkg.Live`](/repos/{repo_id}/graph?node={node_id}) is the export.\n"
    )
    citations = [
        {
            "id": str(node_id),
            "kind": "node",
            "label": "Live",
            "file_path": "src/pipeline.py",
        }
    ]
    await _add_wiki_page(
        db_session,
        repo_id=repo_id,
        slug="overview",
        content=content,
        citations=citations,
    )
    await db_session.commit()

    slug = RepositorySlug(host=repo_host, owner=repo_owner, name=repo_name)
    result = await repair_page_citations(
        session=db_session,
        repository_id=repo_id,
        repo_slug=slug,
        slug="overview",
    )

    assert result.url_format_upgraded == 1
    assert result.raced is False

    # Reload to confirm persistence.
    doc_id = await _document_id(db_session, repo_id, "overview")
    refreshed = await db_session.get(Document, doc_id)
    assert refreshed is not None
    assert (
        f"/repos/example.com/acme/widget/graph?node={node_id}"
        in refreshed.content
    )
    assert f"/repos/{repo_id}/graph?node=" not in refreshed.content


@pytest.mark.asyncio
async def test_repair_page_citations_no_op_when_already_clean(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="pkg.Live")
    content = (
        f"[`pkg.Live`](/repos/example.com/acme/widget/graph?node={node.id}) "
        "is current.\n"
    )
    citations = [
        {
            "id": str(node.id),
            "kind": "node",
            "label": "Live",
            "file_path": "src/pipeline.py",
        }
    ]
    await _add_wiki_page(
        db_session,
        repo_id=repo.id,
        slug="overview",
        content=content,
        citations=citations,
    )
    await db_session.commit()

    slug = RepositorySlug(host=repo.host, owner=repo.owner, name=repo.name)
    result = await repair_page_citations(
        session=db_session,
        repository_id=repo.id,
        repo_slug=slug,
        slug="overview",
    )
    assert result.changed is False
    assert result == RepairResult(
        patched=0,
        dropped=0,
        unchanged=1,
        url_format_upgraded=0,
        raced=False,
        page_loaded=True,
        new_citations=result.new_citations,
        new_content=result.new_content,
    )


@pytest.mark.asyncio
async def test_repair_page_citations_returns_404_shape_when_page_missing(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await db_session.commit()

    slug = RepositorySlug(host=repo.host, owner=repo.owner, name=repo.name)
    result = await repair_page_citations(
        session=db_session,
        repository_id=repo.id,
        repo_slug=slug,
        slug="nonexistent",
    )
    assert result.page_loaded is False


async def _document_id(session: AsyncSession, repo_id: UUID, slug: str) -> UUID:
    from sqlalchemy import select

    return await session.scalar(
        select(Document.id).where(
            Document.repository_id == repo_id,
            Document.slug == slug,
            Document.doc_type == "wiki",
        )
    )
