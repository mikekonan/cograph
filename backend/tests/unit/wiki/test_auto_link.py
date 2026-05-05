"""Tests for `citations.auto_link_qualified_names` (Stage 5b)."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.repository import Repository
from backend.app.wiki.citations import auto_link_qualified_names

pytestmark = pytest.mark.asyncio


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-auto-link",
        name="wiki-auto-link",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="abc",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _add_node(
    session: AsyncSession,
    *,
    repo_id: UUID,
    qn: str,
    name: str | None = None,
    node_type: CodeNodeType = CodeNodeType.STRUCT,
    language: str = "go",
    file_path: str = "pkg/x.go",
) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path=file_path,
        qualified_name=qn,
        node_type=node_type,
        name=name or qn.rsplit(".", 1)[-1],
        language=language,
        start_line=1,
        end_line=10,
        content="...",
        content_hash="x" * 64,
    )
    session.add(node)
    await session.flush()
    return node


async def test_auto_link_wraps_dotted_qualified_name(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="pkg.Generator")

    body = "The pkg.Generator drives codegen."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
    )
    assert count == 1
    assert "[[node:pkg.Generator]]" in out


async def test_auto_link_wraps_backticked_dotted_qn(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="pkg.Generator.Run")

    body = "Call `pkg.Generator.Run` to start."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
    )
    assert count == 1
    assert "[[node:pkg.Generator.Run]]" in out


async def test_auto_link_backticked_single_token_requires_page_scope(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="pkg.Validator")

    body = "Use the `Validator` class."

    # No page scope → not linked.
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
    )
    assert count == 0
    assert "[[node:" not in out

    # With page scope including the node → linked.
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
        page_node_ids=[node.id],
    )
    assert count == 1
    assert "[[node:pkg.Validator]]" in out


async def test_auto_link_skips_bare_single_token_even_when_in_db(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="pkg.Generator")
    body = "Generator drives codegen."  # no backticks
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
        page_node_ids=[node.id],
    )
    assert count == 0
    assert out == body


async def test_auto_link_strips_go_pointer_decorator(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    node = await _add_node(db_session, repo_id=repo.id, qn="pkg.Validator")
    body = "Pass a `*Validator` here."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
        page_node_ids=[node.id],
    )
    assert count == 1
    assert "[[node:pkg.Validator]]" in out


async def test_auto_link_skips_inside_fenced_code_block(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="pkg.Generator")

    body = (
        "Intro pkg.Generator here.\n\n"
        "```go\n"
        "// pkg.Generator should NOT be wrapped inside fences\n"
        "g := pkg.Generator{}\n"
        "```\n\n"
        "Outro pkg.Generator.\n"
    )
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
    )
    # Two intro/outro hits, none in the fence.
    assert count == 2
    fence_start = out.index("```go")
    fence_end = out.index("```\n\n")
    fenced = out[fence_start:fence_end]
    assert "[[node:" not in fenced


async def test_auto_link_skips_inside_existing_placeholder(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="pkg.Generator")

    body = "We already cite [[node:pkg.Generator]] here, no double-link."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
    )
    assert count == 0
    # Original placeholder unchanged, not nested.
    assert out.count("[[node:pkg.Generator]]") == 1


async def test_auto_link_skips_inside_existing_markdown_link(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="pkg.Generator")

    body = "See [pkg.Generator docs](https://example.com/pkg.Generator) for details."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
    )
    assert count == 0
    assert "[[node:" not in out


async def test_auto_link_respects_max_cap(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    await _add_node(db_session, repo_id=repo.id, qn="pkg.A")
    await _add_node(db_session, repo_id=repo.id, qn="pkg.B")
    await _add_node(db_session, repo_id=repo.id, qn="pkg.C")

    body = "pkg.A and pkg.B and pkg.C all matter."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
        max_links=2,
    )
    assert count == 2
    assert out.count("[[node:") == 2


async def test_auto_link_skips_stoplist_words(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    # Even if "Object" is in code_nodes, the stoplist takes precedence.
    node = await _add_node(
        db_session, repo_id=repo.id, qn="pkg.Object", name="Object"
    )
    body = "An `Object` instance."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
        page_node_ids=[node.id],
    )
    assert count == 0
    assert "[[node:" not in out


async def test_auto_link_returns_zero_count_for_unknown_identifiers(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    body = "This pkg.NeverExists won't link."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
    )
    assert count == 0
    assert out == body


async def test_auto_link_drops_ambiguous_single_token(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    # Two distinct nodes share the leaf name "Validator".
    a = await _add_node(
        db_session, repo_id=repo.id, qn="pkg.a.Validator", name="Validator"
    )
    b = await _add_node(
        db_session, repo_id=repo.id, qn="pkg.b.Validator", name="Validator"
    )
    body = "Pass a `Validator` here."
    out, count = await auto_link_qualified_names(
        session=db_session,
        repository_id=repo.id,
        markdown=body,
        page_node_ids=[a.id, b.id],
    )
    # Ambiguous → not auto-linked.
    assert count == 0
    assert "[[node:" not in out
