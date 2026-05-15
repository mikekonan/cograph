"""REST mirror of the MCP `cograph.route` tool.

Lets `cograph-web` and `cograph-eval` call the same source-routing logic
deterministically without going through MCP transport. The handler is a
thin Pydantic-validated wrapper around `route_sources` — identical
behaviour, identical ACL.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.core.deps import (
    get_current_user_optional,
    get_db_session,
    get_settings_dep,
)
from backend.app.models.user import User
from backend.app.rag.source_router import RouteHit, route_sources


router = APIRouter(prefix="/route", tags=["route"])


class RouteRequest(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=3, ge=1, le=10)


class RouteHitResponse(BaseModel):
    kind: Literal["repository", "collection"]
    id: str
    label: str
    score: float
    why: str


class RouteResponse(BaseModel):
    query: str
    repositories: list[RouteHitResponse]
    collections: list[RouteHitResponse]


def _to_response(hit: RouteHit) -> RouteHitResponse:
    return RouteHitResponse(
        kind=hit.kind,  # type: ignore[arg-type]
        id=hit.id,
        label=hit.label,
        score=round(hit.score, 3),
        why=hit.why,
    )


@router.post("", response_model=RouteResponse)
async def route_endpoint(
    payload: RouteRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
    current_user: User | None = Depends(get_current_user_optional),
) -> RouteResponse:
    hits = await route_sources(
        session,
        query=payload.query,
        current_user=current_user,
        settings=settings,
        top_k=payload.top_k,
    )
    return RouteResponse(
        query=payload.query,
        repositories=[_to_response(h) for h in hits if h.kind == "repository"],
        collections=[_to_response(h) for h in hits if h.kind == "collection"],
    )
