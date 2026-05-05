"""Admin endpoints for reusable LLM API secrets — Phase 30.7.

A *secret* is a reusable ``(api_url, api_key)`` pair an admin assigns to
one or more LLM runtime roles via :mod:`admin_llm_runtime`. The actual
API key is stored encrypted via :class:`SecretCipher` and never returned
in responses (only ``has_api_key`` indicates whether it's set).

Owner-only writes (CRUD), admin-or-owner reads. Deletion is blocked while
any ``llm_model_assignments`` row still references the secret.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.secret_service import (
    AdminSecretService,
    SecretTestResult,
    SecretUpsertInput,
)
from backend.app.config import Settings
from backend.app.core.deps import (
    get_db_session,
    require_admin_or_owner,
    require_csrf,
    require_owner,
)
from backend.app.models.llm_secret import LLMSecret
from backend.app.models.user import User

router = APIRouter(prefix="/admin/secrets", tags=["admin-secrets"])


def get_secret_service(request: Request) -> AdminSecretService:
    settings: Settings = request.app.state.settings
    return AdminSecretService(settings)


class LLMSecretResponse(BaseModel):
    id: UUID
    name: str
    api_url: str
    has_api_key: bool
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


class LLMSecretsListResponse(BaseModel):
    items: list[LLMSecretResponse]


class SecretUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    api_url: str
    api_key: str | None = None

    def to_service_input(self) -> SecretUpsertInput:
        return SecretUpsertInput(
            name=self.name,
            api_url=self.api_url,
            api_key=self.api_key,
        )


class SecretTestResponse(BaseModel):
    success: bool
    message: str


def _serialize(row: LLMSecret) -> LLMSecretResponse:
    return LLMSecretResponse(
        id=row.id,
        name=row.name,
        api_url=row.api_url,
        has_api_key=bool(row.api_key_encrypted),
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=LLMSecretsListResponse)
async def list_secrets(
    session: AsyncSession = Depends(get_db_session),
    secret_service: AdminSecretService = Depends(get_secret_service),
    actor: User = Depends(require_admin_or_owner),
) -> LLMSecretsListResponse:
    del actor
    rows = await secret_service.list_secrets(session)
    return LLMSecretsListResponse(items=[_serialize(row) for row in rows])


@router.post(
    "",
    response_model=LLMSecretResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_secret(
    payload: SecretUpsertRequest,
    session: AsyncSession = Depends(get_db_session),
    secret_service: AdminSecretService = Depends(get_secret_service),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> LLMSecretResponse:
    del _csrf
    secret = await secret_service.create_secret(
        session, payload.to_service_input(), actor_id=owner.id
    )
    return _serialize(secret)


@router.put("/{secret_id}", response_model=LLMSecretResponse)
async def update_secret(
    secret_id: UUID,
    payload: SecretUpsertRequest,
    session: AsyncSession = Depends(get_db_session),
    secret_service: AdminSecretService = Depends(get_secret_service),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> LLMSecretResponse:
    del _csrf
    secret = await secret_service.update_secret(
        session, secret_id, payload.to_service_input(), actor_id=owner.id
    )
    return _serialize(secret)


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    secret_service: AdminSecretService = Depends(get_secret_service),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> Response:
    del _csrf, owner
    await secret_service.delete_secret(session, secret_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{secret_id}/test", response_model=SecretTestResponse)
async def test_secret(
    secret_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    secret_service: AdminSecretService = Depends(get_secret_service),
    owner: User = Depends(require_owner),
    _csrf: User = Depends(require_csrf),
) -> SecretTestResponse:
    del _csrf, owner
    result: SecretTestResult = await secret_service.test_secret(session, secret_id)
    return SecretTestResponse(success=result.success, message=result.message)
