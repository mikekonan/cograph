from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from backend.app.config import Settings
from backend.app.core.deps import get_settings_dep
from backend.app.core.errors import ApiError

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def get_health(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> HealthResponse:
    try:
        await request.app.state.session_manager.ping()
    except SQLAlchemyError as exc:
        raise ApiError(
            503,
            "SERVICE_UNAVAILABLE",
            "Database unavailable",
        ) from exc

    return HealthResponse(
        status="healthy",
        database="connected",
        version=settings.version,
    )
