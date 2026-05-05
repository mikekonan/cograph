from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token
from backend.app.models.bank import Bank, BankDocument, BankDocumentChunk
from backend.app.models.enums import UserRole
from backend.app.models.user import User


_CSRF = "csrf-token"


async def _authenticate(client, settings, user: User) -> None:
    """Set access cookie + matching X-CSRF-Token so mutating requests pass."""
    token = create_token(
        user_id=user.id,
        role=user.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)
    client.headers["X-CSRF-Token"] = _CSRF


async def test_list_banks_requires_authentication(client):
    response = await client.get("/api/banks")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_create_bank_requires_csrf(client, db_session, settings):
    """POST /banks without X-CSRF-Token must return 403 CSRF_INVALID."""
    owner = User(email="owner@example.com", password_hash="hashed", role=UserRole.USER)
    db_session.add(owner)
    await db_session.commit()

    # Set access cookie manually — skip _authenticate which also sets CSRF header.
    token = create_token(
        user_id=owner.id,
        role=owner.role,
        settings=settings,
        token_type=TokenType.ACCESS,
        csrf=_CSRF,
    )
    client.cookies.set(settings.auth.access_cookie_name, token)

    response = await client.post("/api/banks", json={"name": "No CSRF"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_list_banks_returns_only_owned_banks_for_regular_user(
    client, db_session, settings
):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    other_user = User(
        email="other@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    owned_bank = Bank(name="Owned", description="mine", owner=owner)
    hidden_bank = Bank(name="Hidden", description="other", owner=other_user)
    db_session.add_all([owned_bank, hidden_bank])
    await db_session.flush()
    db_session.add(
        BankDocument(
            bank_id=owned_bank.id,
            title="ADR",
            source_key="adr/1.md",
            content="# ADR",
            content_hash="hash",
            bytes=5,
            document_metadata={},
        )
    )
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.get("/api/banks")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(owned_bank.id),
                "name": "Owned",
                "description": "mine",
                "owner_id": str(owner.id),
                "document_count": 1,
                "created_at": owned_bank.created_at.isoformat().replace("+00:00", "Z"),
                "updated_at": owned_bank.updated_at.isoformat().replace("+00:00", "Z"),
            }
        ],
        "total": 1,
        "page": 1,
        "per_page": 20,
        "total_pages": 1,
    }


async def test_list_banks_global_owner_sees_all_banks(client, db_session, settings):
    owner = User(
        email="global-owner@example.com",
        password_hash="hashed",
        role=UserRole.OWNER,
    )
    other_user = User(
        email="other@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    db_session.add_all(
        [
            Bank(name="Owned", description="mine", owner=owner),
            Bank(name="Other", description="other", owner=other_user),
        ]
    )
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.get("/api/banks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert {item["name"] for item in payload["items"]} == {"Owned", "Other"}


async def test_create_bank_accepts_max_length_name(client, db_session, settings):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    db_session.add(owner)
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.post(
        "/api/banks",
        json={
            "name": "x" * 255,
            "description": "boundary",
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "x" * 255


async def test_create_bank_rejects_overlong_name(client, db_session, settings):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    db_session.add(owner)
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.post(
        "/api/banks",
        json={
            "name": "x" * 256,
            "description": "too long",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert response.json()["error"]["field_errors"] == [
        {
            "field": "name",
            "code": "INVALID",
            "message": "name must be at most 255 characters",
        }
    ]


async def test_update_bank_rejects_overlong_name(client, db_session, settings):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    bank = Bank(name="Owned", description="mine", owner=owner)
    db_session.add(bank)
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.patch(
        f"/api/banks/{bank.id}",
        json={"name": "x" * 256},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert response.json()["error"]["field_errors"] == [
        {
            "field": "name",
            "code": "INVALID",
            "message": "name must be at most 255 characters",
        }
    ]


async def test_upload_bank_document_json_replaces_existing_document(
    client, db_session, settings
):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    bank = Bank(name="Platform ADRs", description="Architecture", owner=owner)
    db_session.add(bank)
    await db_session.commit()

    await _authenticate(client, settings, owner)

    first_response = await client.post(
        f"/api/banks/{bank.id}/documents",
        json={
            "source_key": "adr/ADR-042.md",
            "content": "# ADR-042\n\nUse first version.\n",
        },
    )
    second_response = await client.post(
        f"/api/banks/{bank.id}/documents",
        json={
            "source_key": "adr/ADR-042.md",
            "content": "# ADR-042\n\nUse second version.\n",
        },
    )

    documents = list(
        (
            await db_session.scalars(
                select(BankDocument).where(BankDocument.bank_id == bank.id)
            )
        ).all()
    )
    chunks = list(
        (
            await db_session.scalars(
                select(BankDocumentChunk).where(
                    BankDocumentChunk.document_id == documents[0].id
                )
            )
        ).all()
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert len(documents) == 1
    assert documents[0].content == "# ADR-042\n\nUse second version.\n"
    assert len(chunks) == second_response.json()["chunk_count"]


async def test_upload_bank_document_multipart_accepts_octet_stream_for_allowed_extension(
    client,
    db_session,
    settings,
):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    bank = Bank(name="Uploads", description=None, owner=owner)
    db_session.add(bank)
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.post(
        f"/api/banks/{bank.id}/documents",
        files={
            "file": (
                "ADR-099.md",
                b"# ADR-099\n\nBinary-tagged body.\n",
                "application/octet-stream",
            ),
        },
    )

    assert response.status_code == 201
    assert response.json()["source_key"] == "ADR-099.md"


async def test_upload_bank_document_multipart_uses_filename_as_default_source_key(
    client,
    db_session,
    settings,
):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    bank = Bank(name="Uploads", description=None, owner=owner)
    db_session.add(bank)
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.post(
        f"/api/banks/{bank.id}/documents",
        files={
            "file": ("ADR-100.md", b"# ADR-100\n\nMultipart body.\n", "text/markdown"),
        },
    )

    documents = list(
        (
            await db_session.scalars(
                select(BankDocument).where(BankDocument.bank_id == bank.id)
            )
        ).all()
    )

    assert response.status_code == 201
    assert response.json()["source_key"] == "ADR-100.md"
    assert len(documents) == 1
    assert documents[0].source_key == "ADR-100.md"


async def test_upload_bank_document_rejects_unsupported_extension_even_with_octet_stream(
    client,
    db_session,
    settings,
):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    bank = Bank(name="Uploads", description=None, owner=owner)
    db_session.add(bank)
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.post(
        f"/api/banks/{bank.id}/documents",
        files={
            "file": (
                "notes.py",
                b"print('not markdown')\n",
                "application/octet-stream",
            ),
        },
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_upload_bank_document_batch_rejects_duplicate_source_keys(
    client,
    db_session,
    settings,
):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    bank = Bank(name="Batch Bank", description=None, owner=owner)
    db_session.add(bank)
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.post(
        f"/api/banks/{bank.id}/documents/batch",
        json={
            "documents": [
                {"source_key": "same.md", "content": "# One"},
                {"source_key": "same.md", "content": "# Two"},
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_get_bank_document_returns_chunks(client, db_session, settings):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
        role=UserRole.USER,
    )
    bank = Bank(name="Docs", description=None, owner=owner)
    document = BankDocument(
        bank=bank,
        title="Guide",
        source_key="guide.md",
        content="# Guide\n\nBody\n",
        content_hash="hash",
        bytes=14,
        document_metadata={},
    )
    db_session.add(document)
    await db_session.flush()
    db_session.add(
        BankDocumentChunk(
            document_id=document.id,
            chunk_index=0,
            heading_path=["Guide"],
            content="# Guide\n\nBody\n",
        )
    )
    await db_session.commit()

    await _authenticate(client, settings, owner)
    response = await client.get(f"/api/banks/{bank.id}/documents/{document.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(document.id),
        "bank_id": str(bank.id),
        "title": "Guide",
        "source_kind": "upload",
        "source_key": "guide.md",
        "external_id": None,
        "content": "# Guide\n\nBody\n",
        "bytes": 14,
        "chunks": [
            {
                "chunk_index": 0,
                "heading_path": ["Guide"],
            }
        ],
        "created_at": document.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": document.updated_at.isoformat().replace("+00:00", "Z"),
    }


async def test_bank_upload_does_not_full_bank_rescan(client, db_session, settings, app):
    """Uploading a single doc calls embed_documents with only that doc's ID,
    not embed_bank which would rescan all documents in the bank."""
    from backend.app.api.banks import get_bank_document_embedder

    owner = User(
        email="rescan-test@example.com", password_hash="hashed", role=UserRole.USER
    )
    bank = Bank(name="Big Bank", owner=owner)
    db_session.add_all([owner, bank])
    await db_session.flush()

    for i in range(10):
        db_session.add(
            BankDocument(
                bank_id=bank.id,
                title=f"Existing Doc {i}",
                source_key=f"existing/{i}.md",
                content=f"# Doc {i}\nContent.",
                content_hash=f"hash{i}",
                bytes=20,
                document_metadata={},
            )
        )
    await db_session.commit()

    mock_embedder = MagicMock()
    mock_embedder.embed_documents = AsyncMock()

    app.dependency_overrides[get_bank_document_embedder] = lambda: mock_embedder
    try:
        await _authenticate(client, settings, owner)
        response = await client.post(
            f"/api/banks/{bank.id}/documents",
            json={
                "source_key": "new-doc.md",
                "content": "# New Document\nFresh content.",
            },
        )
    finally:
        app.dependency_overrides.pop(get_bank_document_embedder, None)

    assert response.status_code == 201
    mock_embedder.embed_documents.assert_called_once()
    call_kwargs = mock_embedder.embed_documents.call_args.kwargs
    # Only the newly uploaded document ID — not all 11 docs in the bank.
    assert len(call_kwargs["document_ids"]) == 1


async def test_bank_upload_skips_fact_extractor_for_unchanged_document(
    client, db_session, settings, app
):
    from backend.app.api.banks import get_bank_fact_extractor

    owner = User(email="owner@example.com", password_hash="hashed", role=UserRole.USER)
    bank = Bank(name="Runbooks", owner=owner)
    db_session.add_all([owner, bank])
    await db_session.commit()

    mock_extractor = MagicMock()
    mock_extractor.extract_documents = AsyncMock()
    app.dependency_overrides[get_bank_fact_extractor] = lambda: mock_extractor

    await _authenticate(client, settings, owner)
    try:
        first = await client.post(
            f"/api/banks/{bank.id}/documents",
            json={
                "source_key": "runbooks/repo.md",
                "content": "# Runbook\n\nRetry when the repo is not ready.\n",
            },
        )
        second = await client.post(
            f"/api/banks/{bank.id}/documents",
            json={
                "source_key": "runbooks/repo.md",
                "content": "# Runbook\n\nRetry when the repo is not ready.\n",
            },
        )
    finally:
        app.dependency_overrides.pop(get_bank_fact_extractor, None)

    assert first.status_code == 201
    assert second.status_code == 201
    mock_extractor.extract_documents.assert_called_once()
