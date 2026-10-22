"""A collection bound to a repository folder mirrors it on every sync."""

from __future__ import annotations

from sqlalchemy import select

from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.llm.md_chunk_embedder import MdChunkEmbedderService
from backend.app.llm.repo_document_embedder import RepoDocumentEmbedderService
from backend.app.models.enums import (
    MdCollectionVisibility,
    RepoSyncRunStatus,
    RepositoryStatus,
    SyncSchedule,
)
from backend.app.models.md_collection import MdChunk, MdCollection, MdDocument, MdLink
from backend.app.models.repo_document import RepoDocument
from backend.app.models.repository import Repository
from backend.app.pipeline.processor import RepoSyncProcessor


async def _make_repo(db_session) -> Repository:
    repo = Repository(
        host="example.com",
        git_url="git@example.com:acme/docs.git",
        name="docs",
        owner="acme",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _make_collection(db_session, repo: Repository | None) -> MdCollection:
    collection = MdCollection(
        name="specs",
        visibility=MdCollectionVisibility.PRIVATE,
        source_repository_id=repo.id if repo else None,
        source_path="docs" if repo else None,
    )
    db_session.add(collection)
    await db_session.commit()
    return collection


def _write_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "docs" / "sub").mkdir(parents=True)
    (checkout / "docs" / ".drafts").mkdir()
    (checkout / "README.md").write_text("# Readme\n\nTop level.\n")
    (checkout / "docs" / "a.md").write_text(
        "# A\n\n| key | value |\n| --- | --- |\n| x | 1 |\n"
    )
    (checkout / "docs" / "sub" / "b.md").write_text("# B\n\nSee [a](../a.md).\n")
    (checkout / "docs" / ".drafts" / "c.md").write_text("# Draft\n")
    return checkout


async def _sync(db_session, repo: Repository, checkout):
    provider = FakeEmbedProvider(dims=8)
    result = await RepoSyncProcessor(
        repo_document_embedder_service=RepoDocumentEmbedderService(provider),
        md_chunk_embedder_service=MdChunkEmbedderService(provider),
    ).process_checkout(session=db_session, repository_id=repo.id, checkout_path=checkout)
    assert result.status is RepoSyncRunStatus.SUCCESS
    return result


async def _mirrored(db_session, collection: MdCollection) -> dict[str, tuple]:
    rows = await db_session.execute(
        select(MdDocument.source_key, MdDocument.id, MdDocument.content_updated_at)
        .where(MdDocument.collection_id == collection.id)
    )
    return {key: (doc_id, updated) for key, doc_id, updated in rows}


async def _repo_docs(db_session, repo: Repository) -> set[str]:
    return set(
        await db_session.scalars(
            select(RepoDocument.file_path).where(RepoDocument.repository_id == repo.id)
        )
    )


async def _link_target(db_session):
    return await db_session.scalar(select(MdLink.target_document_id))


async def test_sync_mirrors_bound_folder(db_session, tmp_path):
    repo = await _make_repo(db_session)
    collection = await _make_collection(db_session, repo)
    checkout = _write_checkout(tmp_path)

    await _sync(db_session, repo, checkout)

    docs = await _mirrored(db_session, collection)
    assert set(docs) == {"a.md", "sub/b.md"}
    assert await _repo_docs(db_session, repo) == {"README.md"}
    # The relative link resolves against b.md's own folder.
    assert await _link_target(db_session) == docs["a.md"][0]
    vectors = await db_session.scalars(select(MdChunk.embedding))
    assert all(vector is not None for vector in vectors)

    # Unchanged files: same rows, nothing embedded again.
    second = await _sync(db_session, repo, checkout)
    assert await _mirrored(db_session, collection) == docs
    assert second.repo_doc_embed_result is not None
    assert second.repo_doc_embed_result.embedded_nodes == 0

    # A deleted file leaves the collection; the link into it is unresolved.
    (checkout / "docs" / "a.md").unlink()
    await _sync(db_session, repo, checkout)
    assert set(await _mirrored(db_session, collection)) == {"sub/b.md"}
    assert await _link_target(db_session) is None


async def test_binding_moves_folder_out_of_repo_docs(db_session, tmp_path):
    repo = await _make_repo(db_session)
    collection = await _make_collection(db_session, None)
    checkout = _write_checkout(tmp_path)

    await _sync(db_session, repo, checkout)
    assert await _repo_docs(db_session, repo) == {
        "README.md",
        "docs/a.md",
        "docs/sub/b.md",
    }
    assert await _mirrored(db_session, collection) == {}

    collection.source_repository_id = repo.id
    collection.source_path = "docs"
    await db_session.commit()
    await _sync(db_session, repo, checkout)

    assert await _repo_docs(db_session, repo) == {"README.md"}
    assert set(await _mirrored(db_session, collection)) == {"a.md", "sub/b.md"}
