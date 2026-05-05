from __future__ import annotations

from sqlalchemy import select

from backend.app.core.auth import TokenType, create_token
from backend.app.models.bank import BankDocument, BankDocumentChunk
from backend.app.models.enums import UserRole
from backend.app.models.user import User


async def test_live_banks_api_supports_create_upload_batch_replace_and_read(
    integration_client,
    integration_session_manager,
    integration_settings,
):
    async with integration_session_manager.session() as session:
        owner = User(
            email="owner@example.com",
            password_hash="hashed",
            role=UserRole.USER,
        )
        session.add(owner)
        await session.commit()
        owner_id = owner.id

    token = create_token(
        user_id=owner_id,
        role=UserRole.USER,
        settings=integration_settings,
        token_type=TokenType.ACCESS,
        csrf="csrf-token",
    )
    integration_client.cookies.set(integration_settings.auth.access_cookie_name, token)
    integration_client.headers["X-CSRF-Token"] = "csrf-token"

    create_response = await integration_client.post(
        "/api/banks",
        json={
            "name": "Platform ADRs",
            "description": "Architecture records",
        },
    )
    assert create_response.status_code == 201
    bank_id = create_response.json()["id"]

    upload_response = await integration_client.post(
        f"/api/banks/{bank_id}/documents",
        json={
            "source_key": "adr/ADR-001.md",
            "content": "# ADR-001\n\nInitial content.\n",
        },
    )
    assert upload_response.status_code == 201
    first_document_id = upload_response.json()["id"]

    batch_response = await integration_client.post(
        f"/api/banks/{bank_id}/documents/batch",
        json={
            "documents": [
                {
                    "source_key": "adr/ADR-001.md",
                    "content": "# ADR-001\n\nUpdated content.\n",
                },
                {
                    "source_key": "adr/ADR-002.md",
                    "content": "# ADR-002\n\nFresh document.\n",
                },
            ]
        },
    )
    assert batch_response.status_code == 201
    assert batch_response.json()["indexed_documents"] == 2

    list_response = await integration_client.get(f"/api/banks/{bank_id}")
    assert list_response.status_code == 200
    assert list_response.json()["documents"]["total"] == 2

    detail_response = await integration_client.get(
        f"/api/banks/{bank_id}/documents/{first_document_id}"
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["content"] == "# ADR-001\n\nUpdated content.\n"

    async with integration_session_manager.session() as session:
        documents = list((await session.scalars(select(BankDocument))).all())
        chunks = list((await session.scalars(select(BankDocumentChunk))).all())

    assert len(documents) == 2
    assert len(chunks) >= 2


async def test_live_banks_api_enforces_name_and_upload_boundaries(
    integration_client,
    integration_session_manager,
    integration_settings,
):
    async with integration_session_manager.session() as session:
        owner = User(
            email="owner@example.com",
            password_hash="hashed",
            role=UserRole.USER,
        )
        session.add(owner)
        await session.commit()
        owner_id = owner.id

    token = create_token(
        user_id=owner_id,
        role=UserRole.USER,
        settings=integration_settings,
        token_type=TokenType.ACCESS,
        csrf="csrf-token",
    )
    integration_client.cookies.set(integration_settings.auth.access_cookie_name, token)
    integration_client.headers["X-CSRF-Token"] = "csrf-token"

    overlong_name_response = await integration_client.post(
        "/api/banks",
        json={
            "name": "x" * 256,
            "description": "too long",
        },
    )
    assert overlong_name_response.status_code == 422
    assert overlong_name_response.json()["error"]["code"] == "VALIDATION_FAILED"

    create_response = await integration_client.post(
        "/api/banks",
        json={
            "name": "Boundary Bank",
            "description": None,
        },
    )
    assert create_response.status_code == 201
    bank_id = create_response.json()["id"]

    bad_upload_response = await integration_client.post(
        f"/api/banks/{bank_id}/documents",
        files={
            "file": ("notes.py", b"print('not markdown')\n", "application/octet-stream"),
        },
    )
    assert bad_upload_response.status_code == 415
    assert bad_upload_response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
