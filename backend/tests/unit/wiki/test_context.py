"""Tests for Stage 1: build_repo_context against a real (sqlite) AsyncSession."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.code_node import CodeNode
from backend.app.models.code_node_summary import CodeNodeSummary
from backend.app.models.document import Document
from backend.app.models.enums import CodeNodeType
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile
from backend.app.wiki.context import build_repo_context

pytestmark = pytest.mark.asyncio


async def _make_repo(session: AsyncSession) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="https://github.com/test/wiki-llm-fixture",
        name="wiki-llm-fixture",
        owner="test",
        branch="main",
        status="ready",
        sync_schedule="manual",
        last_commit="cafef00d",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _add_code_node(
    session: AsyncSession,
    *,
    repo_id,
    qn: str,
    file_path: str,
    summary: str | None = None,
    importance: float = 0.0,
    language: str = "python",
) -> CodeNode:
    node = CodeNode(
        repository_id=repo_id,
        file_path=file_path,
        qualified_name=qn,
        node_type=CodeNodeType.FUNCTION,
        name=qn.rsplit(".", 1)[-1],
        language=language,
        start_line=1,
        end_line=10,
        content="def stub(): pass\n",
        content_hash="x" * 64,
    )
    session.add(node)
    await session.flush()
    if summary is not None:
        session.add(
            CodeNodeSummary(
                code_node_id=node.id,
                repository_id=repo_id,
                summary=summary,
                importance=importance,
                content_hash="y" * 64,
                neighbor_hash="z" * 64,
                model="fake-summary-v1",
            )
        )
    return node


async def _add_source_file(
    session: AsyncSession,
    *,
    repo_id,
    file_path: str,
    content: bytes,
    kind: str = "code",
    language: str = "python",
) -> SourceFile:
    sf = SourceFile(
        repository_id=repo_id,
        file_path=file_path,
        language=language,
        kind=kind,
        raw_bytes=content,
        content_hash="a" * 64,
        bytes=len(content),
    )
    session.add(sf)
    await session.flush()
    return sf


async def test_build_repo_context_assembles_signal(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)

    await _add_source_file(
        db_session,
        repo_id=repo.id,
        file_path="README.md",
        content=b"# wiki-llm-fixture\n\nA tiny project for tests.",
        kind="markdown",
        language="markdown",
    )
    await _add_source_file(
        db_session,
        repo_id=repo.id,
        file_path="src/main.py",
        content=b"print('hi')\n",
    )
    await _add_source_file(
        db_session,
        repo_id=repo.id,
        file_path="src/utils.py",
        content=b"def helper(): pass\n",
    )

    await _add_code_node(
        db_session,
        repo_id=repo.id,
        qn="src.main.run",
        file_path="src/main.py",
        summary="Entry point that wires everything together.",
        importance=0.95,
    )
    await _add_code_node(
        db_session,
        repo_id=repo.id,
        qn="src.utils.helper",
        file_path="src/utils.py",
        summary="Helper used by main.run.",
        importance=0.5,
    )
    await _add_code_node(
        db_session,
        repo_id=repo.id,
        qn="src.utils.unused",
        file_path="src/utils.py",
        # no summary — should not appear in top_summaries
        importance=0.0,
    )

    rd = RepoDocument(
        repository_id=repo.id,
        file_path="docs/intro.md",
        title="Intro",
        content="# Intro\n\nWelcome.",
        content_hash="d" * 64,
        bytes=20,
    )
    db_session.add(rd)
    await db_session.flush()
    db_session.add(
        RepoDocumentChunk(
            document_id=rd.id,
            chunk_index=0,
            heading_path=["Intro"],
            content="Welcome.",
            content_hash="c" * 64,
            mentions=[],
        )
    )

    db_session.add(
        Document(
            repository_id=repo.id,
            slug="getting-started",
            title="Getting started",
            doc_type="wiki",
            sort_order=2,
            content="...",
            content_hash="h" * 64,
            source_hash="h" * 64,
            model="claude-test",
        )
    )
    db_session.add(
        Document(
            repository_id=repo.id,
            slug="index",
            title="Overview",
            doc_type="wiki",
            sort_order=1,
            content="...",
            content_hash="h" * 64,
            source_hash="h" * 64,
            model="claude-test",
        )
    )
    db_session.add(
        Document(
            repository_id=repo.id,
            slug="legacy-page",
            title="Legacy",
            doc_type="brief",
            sort_order=1,
            content="...",
            content_hash="h" * 64,
            source_hash="h" * 64,
            model="legacy",
        )
    )
    await db_session.flush()

    context = await build_repo_context(
        session=db_session,
        repository_id=repo.id,
        commit_sha="cafef00d",
    )

    assert context.repository_id == repo.id
    assert context.commit_sha == "cafef00d"
    assert context.readme_text is not None
    assert "wiki-llm-fixture" in context.readme_text

    file_paths = [entry.file_path for entry in context.file_tree]
    assert "src/main.py" in file_paths
    assert "src/utils.py" in file_paths
    # README is markdown, not 'code', so not in file_tree
    assert "README.md" not in file_paths

    # Top summaries: importance DESC, no rows for code_nodes without a summary.
    assert [s.qualified_name for s in context.top_summaries] == [
        "src.main.run",
        "src.utils.helper",
    ]

    assert [d.file_path for d in context.repo_doc_index] == ["docs/intro.md"]

    # Previous-run slugs respect sort_order and filter by doc_type='wiki'.
    assert context.previous_run_slugs == ["index", "getting-started"]

    assert context.code_node_count == 3
    # Hash fields are stable, deterministic (sha256 hex).
    assert len(context.identity_hash) == 64
    assert len(context.file_tree_hash) == 64


async def test_build_repo_context_handles_empty_repo(db_session: AsyncSession) -> None:
    repo = await _make_repo(db_session)

    context = await build_repo_context(
        session=db_session,
        repository_id=repo.id,
        commit_sha="deadbeef",
    )
    assert context.readme_text is None
    assert context.file_tree == []
    assert context.top_summaries == []
    assert context.repo_doc_index == []
    assert context.previous_run_slugs == []
    assert context.code_node_count == 0
    assert context.identity_hash != ""


async def test_build_repo_context_truncates_long_readme(
    db_session: AsyncSession,
) -> None:
    repo = await _make_repo(db_session)
    big = ("x" * 100 + "\n") * 200  # 20k+ chars
    await _add_source_file(
        db_session,
        repo_id=repo.id,
        file_path="README.md",
        content=big.encode("utf-8"),
        kind="markdown",
        language="markdown",
    )

    context = await build_repo_context(
        session=db_session,
        repository_id=repo.id,
        commit_sha="deadbeef",
        readme_char_cap=500,
    )
    assert context.readme_text is not None
    assert len(context.readme_text) == 500
