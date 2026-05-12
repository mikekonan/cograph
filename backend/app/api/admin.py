from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.deps import get_db_session, require_admin
from backend.app.core.errors import ApiError
from backend.app.models.enums import SyncSchedule
from backend.app.models.repository import Repository
from backend.app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


class RepoWebhookConfigResponse(BaseModel):
    repository_id: UUID
    sync_schedule: SyncSchedule
    webhook_secret: str
    webhook_path: str


@router.get("/repos/{host}/{owner}/{name}/webhook", response_model=RepoWebhookConfigResponse)
async def get_repository_webhook_config(
    host: str,
    owner: str,
    name: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_admin),
) -> RepoWebhookConfigResponse:
    del current_user

    repository = await session.scalar(
        select(Repository).where(
            Repository.host == host,
            Repository.owner == owner,
            Repository.name == name,
            Repository.deleted_at.is_(None),
        )
    )
    if repository is None:
        raise ApiError(404, "NOT_FOUND", "Repository not found")
    if repository.sync_schedule is not SyncSchedule.WEBHOOK or not repository.webhook_secret:
        raise ApiError(409, "WEBHOOK_DISABLED", "Webhook sync is not enabled for this repository")

    return RepoWebhookConfigResponse(
        repository_id=repository.id,
        sync_schedule=repository.sync_schedule,
        webhook_secret=repository.webhook_secret,
        webhook_path=f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/webhook",
    )
