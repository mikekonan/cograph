"""Admin endpoints for the MCP operator briefing.

A *briefing* is the free-form markdown an operator writes to tell every
MCP client what this Cograph deployment is for — team, glossary,
"ask me first" rules. It is appended to the MCP instructions every client
gets when it connects, under `## This deployment`. Clients show only about
2000 characters of instructions, so the briefing gets what the general
playbook leaves: `BRIEFING_LIMIT`. A longer briefing is refused here.

The table `mcp_operator_briefing` is a singleton (CHECK id=1) — there
is exactly one briefing per deployment. Writes require admin or owner.
Reads require admin or owner too (consistent with the rest of
`/api/admin/*`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.deps import (
    get_db_session,
    require_admin_or_owner,
    require_csrf,
)
from backend.app.mcp.instructions import BRIEFING_LIMIT
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing
from backend.app.models.user import User

router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp"])


class McpBriefingResponse(BaseModel):
    content: str
    updated_at: datetime
    updated_by_user_id: UUID | None
    updated_by_email: str | None
    max_length: int = BRIEFING_LIMIT


class McpBriefingPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=0, max_length=BRIEFING_LIMIT)


async def _load_singleton(session: AsyncSession) -> McpOperatorBriefing:
    """Load the singleton row, materialising it on first access.

    The migration seeds `id=1` on PostgreSQL, but the test harness
    bypasses Alembic and builds the schema via `Base.metadata.create_all`,
    which doesn't run the seed INSERT. Lazy-creating here keeps the
    endpoint correct for both paths.
    """

    row = (
        await session.execute(
            select(McpOperatorBriefing).where(McpOperatorBriefing.id == 1)
        )
    ).scalar_one_or_none()
    if row is None:
        row = McpOperatorBriefing(id=1, content="")
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def _serialize(
    session: AsyncSession, row: McpOperatorBriefing
) -> McpBriefingResponse:
    email: str | None = None
    if row.updated_by_user_id is not None:
        email = (
            await session.execute(
                select(User.email).where(User.id == row.updated_by_user_id)
            )
        ).scalar_one_or_none()
    return McpBriefingResponse(
        content=row.content,
        updated_at=row.updated_at,
        updated_by_user_id=row.updated_by_user_id,
        updated_by_email=email,
    )


@router.get("/briefing", response_model=McpBriefingResponse)
async def get_mcp_briefing(
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_admin_or_owner),
) -> McpBriefingResponse:
    del actor
    row = await _load_singleton(session)
    return await _serialize(session, row)


@router.patch("/briefing", response_model=McpBriefingResponse)
async def update_mcp_briefing(
    payload: McpBriefingPatchRequest,
    session: AsyncSession = Depends(get_db_session),
    actor: User = Depends(require_admin_or_owner),
    _csrf: User = Depends(require_csrf),
) -> McpBriefingResponse:
    del _csrf
    row = await _load_singleton(session)
    row.content = payload.content
    row.updated_by_user_id = actor.id
    await session.commit()
    await session.refresh(row)
    return await _serialize(session, row)
