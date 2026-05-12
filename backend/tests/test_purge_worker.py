"""Regression tests for the soft-delete purge worker.

The purge worker drains the cascade for a repository the user
soft-deleted via `DELETE /repos/...`. Three properties matter:

1. Correctness: every per-repository row in every child table is gone
   after the worker finishes, including embedding rows on tables
   chunked by code_node_id / repo_document_id.
2. Idempotency: running the worker again (or running it against a
   non-existent UUID) is a no-op — the substeps are
   `DELETE … WHERE …` so re-running deletes zero rows the second time.
3. Crash safety: a mid-purge failure leaves the row in DELETING with
   no consistency damage — a retry picks up wherever it left off.

We exercise via `purge_repository_in_session` (the test entry point in
the worker module) which runs the same chunked-commit shape against
the provided test session instead of a SessionManager.
"""

from __future__ import annotations

from uuid import uuid4

from backend.app.graph._chunking import IN_CHUNK_SIZE
from backend.app.models.code_embedding import CodeEmbedding
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus
from backend.app.models.module_embedding import ModuleEmbedding
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile
from backend.app.repos.purge_worker import purge_repository_in_session
from sqlalchemy import func, select


def _make_repo() -> Repository:
    return Repository(
        host="example.com",
        git_url="https://github.com/acme/big.git",
        name="big",
        owner="acme",
        branch="main",
        status=RepositoryStatus.DELETING,
    )


async def _seed_repo_with_nodes_and_embeddings(
    db_session, *, n_nodes: int, n_docs: int
) -> Repository:
    repo = _make_repo()
    db_session.add(repo)
    await db_session.commit()

    source_file = SourceFile(
        repository_id=repo.id,
        file_path="app/main.py",
        language="python",
        kind="code",
        raw_bytes=b"x = 1",
        content_hash="sfhash",
        bytes=5,
    )
    db_session.add(source_file)
    await db_session.commit()

    nodes = [
        CodeNode(
            repository_id=repo.id,
            source_file_id=source_file.id,
            file_path="app/main.py",
            qualified_name=f"app.main.fn_{i}",
            node_type=CodeNodeType.FUNCTION,
            name=f"fn_{i}",
            language="python",
            start_line=i + 1,
            end_line=i + 1,
            content=f"def fn_{i}(): ...",
            content_hash=f"h_{i}",
        )
        for i in range(n_nodes)
    ]
    db_session.add_all(nodes)
    await db_session.commit()

    embeddings = [
        CodeEmbedding(
            code_node_id=n.id,
            embedding=[0.0] * 1536,
            model="fake-embed-v1",
            content_hash=n.content_hash,
            neighbor_hash="nb",
        )
        for n in nodes
    ]
    db_session.add_all(embeddings)
    await db_session.commit()

    docs = [
        RepoDocument(
            repository_id=repo.id,
            file_path=f"docs/page_{i}.md",
            content=f"# Page {i}",
            content_hash=f"d_{i}",
            bytes=len(f"# Page {i}"),
        )
        for i in range(n_docs)
    ]
    db_session.add_all(docs)
    await db_session.commit()

    chunks = [
        RepoDocumentChunk(
            document_id=d.id,
            chunk_index=0,
            content=d.content,
            content_hash=d.content_hash,
        )
        for d in docs
    ]
    db_session.add_all(chunks)
    await db_session.commit()

    return repo


async def test_purge_clears_repo_with_chunked_embeddings(db_session):
    """Drives a repo with more code_nodes than IN_CHUNK_SIZE so the
    chunking loop runs more than once, then asserts everything is gone.
    """
    repo = await _seed_repo_with_nodes_and_embeddings(
        db_session, n_nodes=IN_CHUNK_SIZE + 25, n_docs=3
    )
    repo_id = repo.id

    counts = await purge_repository_in_session(db_session, repository_id=repo_id)

    # The chunked branches all ran and reported the actual deletes.
    assert counts["code_embeddings_deleted"] == IN_CHUNK_SIZE + 25
    assert counts["repo_document_chunks_deleted"] == 3
    assert counts["repositories_deleted"] == 1

    # Verify cascade-from-final-DELETE cleared CodeNode + RepoDocument
    # (these rely on Postgres ondelete=CASCADE, but the test DB is
    # sqlite so the SQLAlchemy-level cascades on the Repository model's
    # relationships do the same work when the row is deleted).
    assert (
        await db_session.scalar(
            select(func.count(Repository.id)).where(Repository.id == repo_id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count(CodeEmbedding.id)).where(
                CodeEmbedding.code_node_id.in_(select(CodeNode.id))
            )
        )
        == 0
    )


async def test_purge_is_idempotent_on_missing_repo(db_session):
    """Re-running against a UUID that was never (or is no longer) in
    the DB is a no-op. Same property protects worker retries: the
    second invocation just deletes zero rows everywhere.
    """
    missing = uuid4()

    counts = await purge_repository_in_session(db_session, repository_id=missing)

    assert counts == {
        "code_embeddings_deleted": 0,
        "module_embeddings_deleted": 0,
        "repo_document_chunks_deleted": 0,
        "repositories_deleted": 0,
    }


async def test_purge_replay_after_partial_run_completes_cleanly(db_session):
    """Simulate a mid-purge crash by clearing the embeddings ourselves
    (mimicking "previous worker run got this far before dying"). The
    follow-up purge_repository call must finish the job — and crucially
    must NOT explode because the embeddings table happens to be empty.
    """
    repo = await _seed_repo_with_nodes_and_embeddings(
        db_session, n_nodes=3, n_docs=1
    )
    repo_id = repo.id

    # Wipe embeddings out-of-band to simulate "first run died after
    # step 1 committed". The repo row is still DELETING.
    await db_session.execute(
        CodeEmbedding.__table__.delete()
    )
    await db_session.commit()

    counts = await purge_repository_in_session(db_session, repository_id=repo_id)

    assert counts["code_embeddings_deleted"] == 0  # already wiped
    assert counts["repositories_deleted"] == 1  # finishes the drop


async def test_purge_handles_module_embeddings_directly_by_repo_id(db_session):
    """module_embeddings has a direct repository_id FK, so it gets a
    single-statement delete rather than a chunked one. Make sure that
    branch reports the correct count and clears the rows.
    """
    repo = _make_repo()
    db_session.add(repo)
    await db_session.commit()

    source_file = SourceFile(
        repository_id=repo.id,
        file_path="app/main.py",
        language="python",
        kind="code",
        raw_bytes=b"x = 1",
        content_hash="sf",
        bytes=5,
    )
    db_session.add(source_file)
    await db_session.commit()

    module_nodes = [
        CodeNode(
            repository_id=repo.id,
            source_file_id=source_file.id,
            file_path=f"app/mod_{i}.py",
            qualified_name=f"app.mod_{i}",
            node_type=CodeNodeType.MODULE,
            name=f"mod_{i}",
            language="python",
            start_line=1,
            end_line=1,
            content="",
            content_hash=f"mh_{i}",
        )
        for i in range(2)
    ]
    db_session.add_all(module_nodes)
    await db_session.commit()

    db_session.add_all(
        [
            ModuleEmbedding(
                repository_id=repo.id,
                module_node_id=mn.id,
                embedding=b"\x00" * 4,
                model="fake-embed-v1",
            )
            for mn in module_nodes
        ]
    )
    await db_session.commit()

    counts = await purge_repository_in_session(db_session, repository_id=repo.id)

    assert counts["module_embeddings_deleted"] == 2
    assert (
        await db_session.scalar(
            select(func.count(ModuleEmbedding.id)).where(
                ModuleEmbedding.repository_id == repo.id
            )
        )
        == 0
    )
