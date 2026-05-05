from __future__ import annotations

from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType
from backend.app.models.enums import RepositoryStatus, RepositoryVisibility, SyncSchedule
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository


async def test_list_repository_documents_returns_counts(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    code_node = CodeNode(
        repository_id=repository.id,
        file_path="service.py",
        qualified_name="service.helper",
        node_type=CodeNodeType.FUNCTION,
        name="helper",
        language="python",
        start_line=1,
        end_line=2,
        content="def helper():\n    return 1\n",
        signature="helper()",
        doc_comment=None,
        callers=[],
        callees=[],
        node_metadata={},
        content_hash="hash",
    )
    document = RepoDocument(
        repository_id=repository.id,
        file_path="README.md",
        title="Demo",
        content="# Demo",
        content_hash="doc-hash",
        bytes=6,
    )
    db_session.add_all([code_node, document])
    await db_session.flush()
    db_session.add(
        RepoDocumentChunk(
            document_id=document.id,
            chunk_index=0,
            heading_path=["Demo"],
            content="# Demo",
            mentions=[str(code_node.id)],
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/documents")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["per_page"] == 20
    assert data["total_pages"] == 1
    item = data["items"][0]
    assert item["id"] == str(document.id)
    assert item["repository_id"] == str(repository.id)
    assert item["file_path"] == "README.md"
    assert item["title"] == "Demo"
    assert item["bytes"] == 6
    assert item["chunk_count"] == 1
    assert item["mentions_count"] == 1
    # excerpt field must be present (content of first chunk, stripped)
    assert "excerpt" in item


async def test_list_repository_documents_hides_admin_only_repo_from_anonymous(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/documents")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_repository_document_returns_chunk_mentions(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    code_node = CodeNode(
        repository_id=repository.id,
        file_path="service.py",
        qualified_name="service.helper",
        node_type=CodeNodeType.FUNCTION,
        name="helper",
        language="python",
        start_line=1,
        end_line=2,
        content="def helper():\n    return 1\n",
        signature="helper()",
        doc_comment=None,
        callers=[],
        callees=[],
        node_metadata={},
        content_hash="hash",
    )
    document = RepoDocument(
        repository_id=repository.id,
        file_path="README.md",
        title="Demo",
        content="# Demo\n\nUse `helper`.",
        content_hash="doc-hash",
        bytes=21,
    )
    db_session.add_all([code_node, document])
    await db_session.flush()
    chunk = RepoDocumentChunk(
        document_id=document.id,
        chunk_index=0,
        heading_path=["Demo"],
        content="# Demo\n\nUse `helper`.",
        mentions=[str(code_node.id)],
    )
    db_session.add(chunk)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/documents/{document.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(document.id)
    assert data["repository_id"] == str(repository.id)
    assert data["file_path"] == "README.md"
    assert data["title"] == "Demo"
    assert data["content"] == "# Demo\n\nUse `helper`."
    assert data["bytes"] == 21
    assert len(data["chunks"]) == 1
    chunk_resp = data["chunks"][0]
    # chunk must expose its UUID for graph navigation
    assert chunk_resp["id"] == str(chunk.id)
    assert chunk_resp["chunk_index"] == 0
    assert chunk_resp["heading_path"] == ["Demo"]
    assert chunk_resp["mentions"] == [
        {
            "node_id": str(code_node.id),
            "name": "helper",
            "file_path": "service.py",
        }
    ]


async def test_get_repository_document_hides_admin_only_repo_from_anonymous(client, db_session):
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=RepositoryVisibility.ADMIN_ONLY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    document = RepoDocument(
        repository_id=repository.id,
        file_path="README.md",
        title="Demo",
        content="# Demo\n",
        content_hash="doc-hash",
        bytes=7,
    )
    db_session.add(document)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/documents/{document.id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_list_documents_returns_excerpt(client, db_session):
    """First chunk content (stripped of markdown) appears as excerpt on list items."""
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/excerpt.git",
        name="excerpt",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    document = RepoDocument(
        repository_id=repository.id,
        file_path="GUIDE.md",
        title="Guide",
        content="# Guide\n\nIntro.",
        content_hash="guide-hash",
        bytes=17,
    )
    db_session.add(document)
    await db_session.flush()

    first_chunk_content = "# Guide\n\nThis is the first chunk with some **bold** and `code`."
    second_chunk_content = "## Section two\n\nMore content here."
    db_session.add(
        RepoDocumentChunk(
            document_id=document.id,
            chunk_index=0,
            heading_path=["Guide"],
            content=first_chunk_content,
            mentions=[],
        )
    )
    db_session.add(
        RepoDocumentChunk(
            document_id=document.id,
            chunk_index=1,
            heading_path=["Guide", "Section two"],
            content=second_chunk_content,
            mentions=[],
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/documents")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    excerpt = items[0]["excerpt"]
    assert excerpt is not None
    # Excerpt must come from chunk 0, not chunk 1
    assert "Section two" not in excerpt
    # Markdown headings and inline markup must be stripped
    assert "#" not in excerpt
    assert "**" not in excerpt
    assert "`" not in excerpt
    # Content must be non-empty and within 280 chars
    assert 0 < len(excerpt) <= 280


async def test_list_documents_excerpt_null_when_no_chunks(client, db_session):
    """Documents with zero chunks must return excerpt: null."""
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/nochunks.git",
        name="nochunks",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    document = RepoDocument(
        repository_id=repository.id,
        file_path="EMPTY.md",
        title="Empty",
        content="",
        content_hash="empty-hash",
        bytes=0,
    )
    db_session.add(document)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/documents")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["excerpt"] is None


async def test_detail_includes_chunk_ids(client, db_session):
    """Each chunk in the detail response must expose its UUID id field."""
    repository = Repository(
        host="example.com",
        git_url="https://example.com/acme/chunkids.git",
        name="chunkids",
        owner="acme",
        branch="main",
        status=RepositoryStatus.READY,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    document = RepoDocument(
        repository_id=repository.id,
        file_path="DOC.md",
        title="Doc",
        content="# Doc\n\nParagraph one.\n\n## Section\n\nParagraph two.",
        content_hash="doc-id-hash",
        bytes=50,
    )
    db_session.add(document)
    await db_session.flush()

    chunk_a = RepoDocumentChunk(
        document_id=document.id,
        chunk_index=0,
        heading_path=["Doc"],
        content="# Doc\n\nParagraph one.",
        mentions=[],
    )
    chunk_b = RepoDocumentChunk(
        document_id=document.id,
        chunk_index=1,
        heading_path=["Doc", "Section"],
        content="## Section\n\nParagraph two.",
        mentions=[],
    )
    db_session.add_all([chunk_a, chunk_b])
    await db_session.commit()

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/documents/{document.id}")

    assert response.status_code == 200
    chunks = response.json()["chunks"]
    assert len(chunks) == 2
    chunk_ids = {c["id"] for c in chunks}
    # Each chunk must carry a UUID id matching the DB rows
    assert str(chunk_a.id) in chunk_ids
    assert str(chunk_b.id) in chunk_ids
