"""Tests for md-collections API including job tracking."""

from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

from backend.app.core.auth import TokenType, create_token
from backend.app.models.enums import MdJobKind, MdJobStatus, UserRole
from backend.app.models.md_collection import MdCollection, MdJob
from backend.app.models.personal_access_token import PersonalAccessToken
from backend.app.models.user import User

_TEST_CSRF = "csrf-token"


async def _auth_user(client, settings, user: User) -> None:
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_TEST_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.cookies.set(settings.auth.csrf_cookie_name, _TEST_CSRF)


def _hash_pat(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


async def _mint_pat(
    db_session,
    user: User,
    plaintext: str,
    *,
    scopes: list[str] | None = None,
) -> dict[str, str]:
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="test-token",
            token_hash=_hash_pat(plaintext),
            token_prefix=plaintext[:16],
            scopes=scopes or ["api:read"],
        )
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {plaintext}"}


@pytest.fixture
async def user(db_session):
    u = User(
        email="user@test.com", password_hash="secret", name="User", role=UserRole.USER
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def collection(db_session, user):
    col = MdCollection(
        name="test-col", description="", visibility="private", owner_id=user.id
    )
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)
    return col


async def test_list_jobs_empty(client, settings, user, collection):
    await _auth_user(client, settings, user)
    response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []


async def test_list_jobs_returns_items(client, settings, user, collection, db_session):
    await _auth_user(client, settings, user)
    job = MdJob(
        collection_id=collection.id,
        kind=MdJobKind.EMBED,
        status=MdJobStatus.SUCCESS,
        result_summary={"embedded_nodes": 5},
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["kind"] == "embed"
    assert payload["items"][0]["status"] == "success"
    assert payload["items"][0]["result_summary"]["embedded_nodes"] == 5


async def test_list_jobs_visible_to_authenticated_user(
    client, settings, collection, db_session
):
    other = User(
        email="other@test.com", password_hash="secret", name="Other", role=UserRole.USER
    )
    db_session.add(other)
    await db_session.commit()
    await _auth_user(client, settings, other)

    response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    assert response.status_code == 200


async def test_global_owner_can_read_private_collection(
    client,
    settings,
    collection,
    db_session,
):
    owner = User(
        email="global-owner@test.com",
        password_hash="secret",
        name="Global Owner",
        role=UserRole.OWNER,
    )
    db_session.add(owner)
    await db_session.commit()
    await _auth_user(client, settings, owner)

    response = await client.get(f"/api/md-collections/{collection.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(collection.id)


async def test_upload_triggers_jobs(client, settings, user, collection):
    await _auth_user(client, settings, user)
    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={"documents": [{"source_key": "hello.md", "content": "# Hello\nworld"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["indexed_documents"] == 1

    # Jobs should have been created
    jobs_response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    jobs_payload = jobs_response.json()
    assert len(jobs_payload["items"]) == 2
    kinds = {j["kind"] for j in jobs_payload["items"]}
    assert kinds == {"embed", "resolve_links"}


async def test_batch_upload_rejects_empty_source_key(
    client, settings, user, collection
):
    await _auth_user(client, settings, user)
    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={"documents": [{"source_key": "   ", "content": "# Empty key"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert payload["error"]["field_errors"][0]["field"] == "source_key"


async def test_single_json_upload_rejects_empty_source_key(
    client, settings, user, collection
):
    await _auth_user(client, settings, user)
    response = await client.post(
        f"/api/md-collections/{collection.id}/documents",
        json={"source_key": "   ", "content": "# Empty key"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert payload["error"]["field_errors"][0]["field"] == "source_key"


async def test_upload_does_not_crash_when_embedder_unavailable(
    client, settings, user, collection
):
    """Regression: upload must return 201 even when no OpenAI key is configured.

    Before the fix the endpoint called embedder.embed_documents() synchronously
    inside the HTTP handler, so a missing key produced 500.  After the fix
    embedding happens only in the background worker.
    """
    await _auth_user(client, settings, user)
    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={"documents": [{"source_key": "hello.md", "content": "# Hello\nworld"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["indexed_documents"] == 1

    # Jobs must still be created in DB (worker will fail later, not the endpoint)
    jobs_response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    jobs_payload = jobs_response.json()
    assert len(jobs_payload["items"]) == 2


async def test_upload_unchanged_documents_does_not_create_new_jobs(
    client, settings, user, collection
):
    """Uploading the same documents twice should not enqueue duplicate jobs."""
    await _auth_user(client, settings, user)

    # First upload — new documents
    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={"documents": [{"source_key": "hello.md", "content": "# Hello\nworld"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["indexed_documents"] == 1
    assert payload["unchanged_documents"] == 0

    jobs_response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    first_job_count = len(jobs_response.json()["items"])
    assert first_job_count == 2

    # Second upload — identical content
    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={"documents": [{"source_key": "hello.md", "content": "# Hello\nworld"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["indexed_documents"] == 1
    assert payload["unchanged_documents"] == 1

    jobs_response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    second_job_count = len(jobs_response.json()["items"])
    assert second_job_count == first_job_count, (
        "unchanged upload must not create new background jobs"
    )


async def test_create_collection_rejects_duplicate_name(client, settings, user):
    await _auth_user(client, settings, user)
    response = await client.post(
        "/api/md-collections",
        json={"name": "duplicate-col", "description": "first"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201

    response = await client.post(
        "/api/md-collections",
        json={"name": "duplicate-col", "description": "second"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["error"]["code"] == "DUPLICATE_NAME"


async def test_update_collection_rejects_duplicate_name(
    client, settings, user, collection, db_session
):
    await _auth_user(client, settings, user)
    other = MdCollection(
        name="other-col", description="", visibility="private", owner_id=user.id
    )
    db_session.add(other)
    await db_session.commit()

    response = await client.patch(
        f"/api/md-collections/{collection.id}",
        json={"name": "other-col"},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["error"]["code"] == "DUPLICATE_NAME"


async def test_list_document_chunks(client, settings, user, collection, db_session):
    await _auth_user(client, settings, user)
    # Create a document with chunks
    from backend.app.models.md_collection import MdChunk, MdDocument

    doc = MdDocument(
        collection_id=collection.id,
        source_key="test.md",
        title="Test",
        content="# Hello\nworld",
        content_hash="abc",
        bytes=20,
        word_count=2,
        line_count=2,
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    chunk = MdChunk(
        document_id=doc.id,
        chunk_index=0,
        heading_path=["Hello"],
        heading_level=1,
        content="world",
    )
    db_session.add(chunk)
    await db_session.commit()

    response = await client.get(
        f"/api/md-collections/{collection.id}/documents/{doc.id}/chunks"
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["chunk_index"] == 0
    assert payload["items"][0]["heading_path"] == ["Hello"]
    assert payload["items"][0]["heading_level"] == 1
    assert payload["items"][0]["content"] == "world"


async def test_retry_md_job(client, settings, user, collection, db_session):
    await _auth_user(client, settings, user)
    # Create a failed job
    job = MdJob(
        collection_id=collection.id,
        kind=MdJobKind.EMBED,
        status=MdJobStatus.ERROR,
        result_summary={"processed": 0, "total": 10},
        error_message="OpenAI key missing",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    response = await client.post(
        f"/api/md-collections/-/jobs/{job.id}/retry",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "embed"
    assert payload["status"] == "queued"
    assert payload["id"] != str(job.id)

    # Original job count + new retry job = 2 jobs for the collection
    jobs_response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    assert len(jobs_response.json()["items"]) == 2


async def test_retry_md_job_not_found(client, settings, user):
    await _auth_user(client, settings, user)
    from uuid import uuid4

    response = await client.post(
        f"/api/md-collections/-/jobs/{uuid4()}/retry",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 404


async def test_retry_md_job_requires_csrf(
    client, settings, user, collection, db_session
):
    await _auth_user(client, settings, user)
    job = MdJob(
        collection_id=collection.id,
        kind=MdJobKind.EMBED,
        status=MdJobStatus.ERROR,
        result_summary={},
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.post(f"/api/md-collections/-/jobs/{job.id}/retry")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_retry_md_job_access_denied(client, settings, collection, db_session):
    other = User(
        email="other@test.com", password_hash="secret", name="Other", role=UserRole.USER
    )
    db_session.add(other)
    await db_session.commit()
    await _auth_user(client, settings, other)

    job = MdJob(
        collection_id=collection.id,
        kind=MdJobKind.EMBED,
        status=MdJobStatus.ERROR,
        result_summary={},
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.post(
        f"/api/md-collections/-/jobs/{job.id}/retry",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


async def test_retry_public_collection_job_denied_for_non_owner(
    client, settings, db_session
):
    owner = User(
        email="retry-owner@test.com",
        password_hash="secret",
        name="Owner",
        role=UserRole.USER,
    )
    intruder = User(
        email="retry-intruder@test.com",
        password_hash="secret",
        name="Intruder",
        role=UserRole.USER,
    )
    db_session.add_all([owner, intruder])
    await db_session.commit()
    await db_session.refresh(owner)
    await db_session.refresh(intruder)

    col = MdCollection(
        name="retry-public-col",
        description="",
        visibility="public",
        owner_id=owner.id,
    )
    db_session.add(col)
    await db_session.flush()
    job = MdJob(
        collection_id=col.id,
        kind=MdJobKind.EMBED,
        status=MdJobStatus.ERROR,
        result_summary={},
    )
    db_session.add(job)
    await db_session.commit()

    await _auth_user(client, settings, intruder)
    response = await client.post(
        f"/api/md-collections/-/jobs/{job.id}/retry",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_embed_status_empty_collection(client, settings, user, collection):
    await _auth_user(client, settings, user)
    response = await client.get(f"/api/md-collections/{collection.id}/embed-status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_chunks"] == 0
    assert payload["embedded_chunks"] == 0
    assert payload["is_ready"] is False


@pytest.mark.asyncio
async def test_embed_status_with_chunks(client, settings, user, collection, db_session):
    from backend.app.models.md_collection import MdDocument, MdChunk

    await _auth_user(client, settings, user)
    doc = MdDocument(
        collection_id=collection.id,
        source_key="test.md",
        title="Test",
        content="hello world",
        content_hash="abc123",
        bytes=11,
        word_count=2,
        line_count=1,
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = MdChunk(
        document_id=doc.id,
        chunk_index=0,
        heading_path=[],
        content="hello world",
        content_hash="abc",
        embedding=[0.1] * 1536,
        model="text-embedding-3-small",
    )
    db_session.add(chunk)
    await db_session.commit()

    response = await client.get(f"/api/md-collections/{collection.id}/embed-status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_chunks"] == 1
    assert payload["embedded_chunks"] == 1
    assert payload["is_ready"] is True


@pytest.mark.asyncio
async def test_embed_status_visible_to_authenticated_user(
    client, settings, collection, db_session
):
    other = User(
        email="other@test.com", password_hash="secret", name="Other", role=UserRole.USER
    )
    db_session.add(other)
    await db_session.commit()
    await _auth_user(client, settings, other)

    response = await client.get(f"/api/md-collections/{collection.id}/embed-status")
    assert response.status_code == 200


async def test_reembed_collection_success(client, settings, user, collection):
    await _auth_user(client, settings, user)
    response = await client.post(
        f"/api/md-collections/{collection.id}/re-embed",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "embed"
    assert payload["status"] == "queued"

    # Verify a new job was created
    jobs_response = await client.get(f"/api/md-collections/{collection.id}/jobs")
    assert len(jobs_response.json()["items"]) == 1


async def test_reembed_collection_not_found(client, settings, user):
    await _auth_user(client, settings, user)
    from uuid import uuid4

    response = await client.post(
        f"/api/md-collections/{uuid4()}/re-embed",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 404


async def test_reembed_collection_requires_csrf(client, settings, user, collection):
    await _auth_user(client, settings, user)
    response = await client.post(f"/api/md-collections/{collection.id}/re-embed")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_reembed_collection_access_denied(
    client, settings, collection, db_session
):
    other = User(
        email="other@test.com", password_hash="secret", name="Other", role=UserRole.USER
    )
    db_session.add(other)
    await db_session.commit()
    await _auth_user(client, settings, other)

    response = await client.post(
        f"/api/md-collections/{collection.id}/re-embed",
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


class _StubEmbedProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


class _StubMdRetriever:
    async def retrieve(self, session, **kwargs):
        from backend.app.rag.retriever import RetrievedChunk
        from uuid import uuid4

        del session
        return [
            RetrievedChunk(
                store="md_collections",
                chunk_id=uuid4(),
                content="stub chunk",
                score=0.9,
                metadata={
                    "document_id": uuid4(),
                    "source_key": "doc.md",
                    "title": "Doc",
                    "heading_path": ["Section"],
                    "vector_rank": 1,
                    "lexical_rank": 2,
                },
            )
        ]


@pytest.mark.asyncio
async def test_search_md_collection_returns_results(
    client, settings, user, collection, app
):
    await _auth_user(client, settings, user)

    from backend.app.api.md_collections import (
        get_md_search_embed_provider,
        get_md_hybrid_retriever,
    )

    app.dependency_overrides[get_md_search_embed_provider] = lambda: (
        _StubEmbedProvider()
    )
    app.dependency_overrides[get_md_hybrid_retriever] = lambda: _StubMdRetriever()

    try:
        response = await client.post(
            f"/api/md-collections/{collection.id}/search",
            json={"query": "how does auth work", "top_k": 5},
        )
    finally:
        app.dependency_overrides.pop(get_md_search_embed_provider, None)
        app.dependency_overrides.pop(get_md_hybrid_retriever, None)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["content"] == "stub chunk"
    assert result["score"] == 0.9
    assert result["source_key"] == "doc.md"
    assert result["vector_rank"] == 1
    assert result["lexical_rank"] == 2


@pytest.mark.asyncio
async def test_search_md_collection_visible_to_authenticated_user(
    client, settings, collection, db_session, app
):
    other = User(
        email="other@test.com", password_hash="secret", name="Other", role=UserRole.USER
    )
    db_session.add(other)
    await db_session.commit()
    await _auth_user(client, settings, other)

    from backend.app.api.md_collections import (
        get_md_search_embed_provider,
        get_md_hybrid_retriever,
    )

    app.dependency_overrides[get_md_search_embed_provider] = lambda: (
        _StubEmbedProvider()
    )
    app.dependency_overrides[get_md_hybrid_retriever] = lambda: _StubMdRetriever()

    try:
        response = await client.post(
            f"/api/md-collections/{collection.id}/search",
            json={"query": "how does auth work", "top_k": 5},
        )
    finally:
        app.dependency_overrides.pop(get_md_search_embed_provider, None)
        app.dependency_overrides.pop(get_md_hybrid_retriever, None)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_upload_to_public_collection_denied_for_non_owner(
    client, settings, db_session, app
):
    """Non-owner must NOT be able to upload documents to a public collection."""
    owner = User(
        email="owner@test.com", password_hash="secret", name="Owner", role=UserRole.USER
    )
    intruder = User(
        email="intruder@test.com",
        password_hash="secret",
        name="Intruder",
        role=UserRole.USER,
    )
    db_session.add_all([owner, intruder])
    await db_session.commit()
    await db_session.refresh(owner)
    await db_session.refresh(intruder)

    col = MdCollection(
        name="public-col", description="", visibility="public", owner_id=owner.id
    )
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)

    await _auth_user(client, settings, intruder)
    response = await client.post(
        f"/api/md-collections/{col.id}/documents/batch",
        json={"documents": [{"source_key": "x.md", "content": "hello"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_upload_to_public_collection(client, settings, db_session, app):
    """Owner must be able to upload to their public collection."""
    owner = User(
        email="owner2@test.com",
        password_hash="secret",
        name="Owner2",
        role=UserRole.USER,
    )
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)

    col = MdCollection(
        name="public-col-2", description="", visibility="public", owner_id=owner.id
    )
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)

    await _auth_user(client, settings, owner)
    response = await client.post(
        f"/api/md-collections/{col.id}/documents/batch",
        json={"documents": [{"source_key": "x.md", "content": "hello"}]},
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_anonymous_can_read_public_collection(client, settings, db_session, app):
    """Anonymous users must be able to GET a public collection."""
    owner = User(
        email="owner3@test.com",
        password_hash="secret",
        name="Owner3",
        role=UserRole.USER,
    )
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)

    col = MdCollection(
        name="public-col-3", description="", visibility="public", owner_id=owner.id
    )
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)

    # No auth cookies
    response = await client.get(f"/api/md-collections/{col.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "public-col-3"


@pytest.mark.asyncio
async def test_anonymous_cannot_read_private_collection(
    client, settings, db_session, app
):
    """Anonymous users must NOT be able to GET a private collection."""
    owner = User(
        email="owner4@test.com",
        password_hash="secret",
        name="Owner4",
        role=UserRole.USER,
    )
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)

    col = MdCollection(
        name="private-col", description="", visibility="private", owner_id=owner.id
    )
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)

    # No auth cookies
    response = await client.get(f"/api/md-collections/{col.id}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_pat_user_can_read_private_collection(
    client, settings, db_session, app
):
    owner = User(
        email="owner5@test.com",
        password_hash="secret",
        name="Owner5",
        role=UserRole.USER,
    )
    reader = User(
        email="reader@test.com",
        password_hash="secret",
        name="Reader",
        role=UserRole.USER,
    )
    db_session.add_all([owner, reader])
    await db_session.commit()
    await db_session.refresh(owner)
    await db_session.refresh(reader)

    col = MdCollection(
        name="private-pat-col",
        description="",
        visibility="private",
        owner_id=owner.id,
    )
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)
    headers = await _mint_pat(
        db_session,
        reader,
        "cgr_pat_md_read_member_token_00000000000000000000000000",
    )

    response = await client.get(f"/api/md-collections/{col.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == str(col.id)


@pytest.mark.asyncio
async def test_pat_without_api_read_cannot_read_collection(
    client, settings, db_session, app
):
    owner = User(
        email="owner6@test.com",
        password_hash="secret",
        name="Owner6",
        role=UserRole.USER,
    )
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)

    col = MdCollection(
        name="private-pat-scope-col",
        description="",
        visibility="private",
        owner_id=owner.id,
    )
    db_session.add(col)
    await db_session.commit()
    await db_session.refresh(col)
    headers = await _mint_pat(
        db_session,
        owner,
        "cgr_pat_md_mcp_only_token_000000000000000000000000000",
        scopes=["mcp"],
    )

    response = await client.get(f"/api/md-collections/{col.id}", headers=headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_pat_with_api_write_can_upload_single_document(
    client, settings, db_session, user, collection
):
    """PAT with api:write must be able to push a single doc with no CSRF."""
    headers = await _mint_pat(
        db_session,
        user,
        "cgr_pat_md_upload_single_token_000000000000000000000000",
        scopes=["api:write"],
    )

    response = await client.post(
        f"/api/md-collections/{collection.id}/documents",
        json={"source_key": "from-pat.md", "content": "# Hello\n"},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["source_key"] == "from-pat.md"


@pytest.mark.asyncio
async def test_pat_without_api_write_rejected_on_upload(
    client, settings, db_session, user, collection
):
    """PAT minted with mcp scope only must get 403 INSUFFICIENT_SCOPE on POST."""
    headers = await _mint_pat(
        db_session,
        user,
        "cgr_pat_md_upload_noscope_token_00000000000000000000000",
        scopes=["api:read"],
    )

    response = await client.post(
        f"/api/md-collections/{collection.id}/documents",
        json={"source_key": "denied.md", "content": "# Denied\n"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


@pytest.mark.asyncio
async def test_pat_with_api_write_can_upload_batch(
    client, settings, db_session, user, collection
):
    """PAT with api:write must be able to push a batch with no CSRF."""
    headers = await _mint_pat(
        db_session,
        user,
        "cgr_pat_md_upload_batch_token_0000000000000000000000000",
        scopes=["api:write"],
    )

    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={"documents": [{"source_key": "pat-batch.md", "content": "# Hi\n"}]},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["indexed_documents"] == 1


@pytest.mark.asyncio
async def test_unknown_pat_returns_401(client, settings, db_session, collection):
    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={"documents": [{"source_key": "x.md", "content": "# x"}]},
        headers={"Authorization": "Bearer cgr_pat_unknown_token_zzzzzzzzzzzzzzzzzzzz"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_batch_upload_creates_upload_job_when_total_supplied(
    client, settings, user, collection, db_session
):
    """First batch with upload_total creates a kind=upload MdJob, status running."""
    await _auth_user(client, settings, user)

    response = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={
            "documents": [{"source_key": "first.md", "content": "# first"}],
            "upload_total": 5,
        },
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert response.status_code == 201, response.text
    upload_job_id = response.json()["upload_job_id"]
    assert upload_job_id is not None

    job = await db_session.get(MdJob, UUID(upload_job_id))
    assert job is not None
    assert job.kind is MdJobKind.UPLOAD
    assert job.status is MdJobStatus.RUNNING
    assert job.result_summary["total"] == 5
    assert job.result_summary["processed"] == 1


@pytest.mark.asyncio
async def test_batch_upload_attaches_to_existing_upload_job_and_finishes(
    client, settings, user, collection, db_session
):
    """Second batch attaches via upload_job_id; final flips to success."""
    await _auth_user(client, settings, user)

    first = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={
            "documents": [{"source_key": "a.md", "content": "# a"}],
            "upload_total": 2,
        },
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert first.status_code == 201
    upload_job_id = first.json()["upload_job_id"]

    second = await client.post(
        f"/api/md-collections/{collection.id}/documents/batch",
        json={
            "documents": [{"source_key": "b.md", "content": "# b"}],
            "upload_job_id": upload_job_id,
            "upload_final": True,
        },
        headers={"X-CSRF-Token": _TEST_CSRF},
    )
    assert second.status_code == 201
    assert second.json()["upload_job_id"] == upload_job_id

    db_session.expire_all()
    job = await db_session.get(MdJob, UUID(upload_job_id))
    assert job is not None
    assert job.status is MdJobStatus.SUCCESS
    assert job.result_summary["processed"] == 2
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_batch_upload_rejects_upload_job_id_from_other_collection(
    client, settings, user, db_session
):
    """An upload_job_id pointing at a different collection must 403."""
    col_a = MdCollection(
        name="upload-job-col-a",
        description="",
        visibility="private",
        owner_id=user.id,
    )
    col_b = MdCollection(
        name="upload-job-col-b",
        description="",
        visibility="private",
        owner_id=user.id,
    )
    db_session.add_all([col_a, col_b])
    await db_session.commit()
    await db_session.refresh(col_a)
    await db_session.refresh(col_b)

    foreign_job = MdJob(
        collection_id=col_a.id,
        kind=MdJobKind.UPLOAD,
        status=MdJobStatus.RUNNING,
        result_summary={"total": 1, "processed": 0, "failed": 0, "current_item": None},
    )
    db_session.add(foreign_job)
    await db_session.commit()
    await db_session.refresh(foreign_job)

    await _auth_user(client, settings, user)
    response = await client.post(
        f"/api/md-collections/{col_b.id}/documents/batch",
        json={
            "documents": [{"source_key": "x.md", "content": "# x"}],
            "upload_job_id": str(foreign_job.id),
        },
        headers={"X-CSRF-Token": _TEST_CSRF},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
