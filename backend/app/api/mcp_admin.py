"""Admin endpoints for the MCP operator briefing.

A *briefing* is the free-form markdown an operator writes to tell every
MCP client what this Cograph deployment is for — team, glossary,
"ask me first" rules. It's surfaced to agents two ways:

- inlined into the FastMCP `instructions=` payload sent at `initialize`,
- and exposed as the `cograph://briefing` resource so agents can
  re-fetch it after a context-compaction.

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

from backend.app.config import Settings
from backend.app.core.deps import (
    get_db_session,
    get_settings_dep,
    require_admin_or_owner,
    require_csrf,
)
from backend.app.mcp.instructions import refresh_cached_instructions
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing
from backend.app.models.user import User

router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp"])

# Same cap as the column / Settings.mcp.briefing_max_length; defended
# at the API layer too so an oversized PATCH 400s instead of choking
# on the playbook render.
_BRIEFING_MAX_LENGTH = 8000


class McpBriefingResponse(BaseModel):
    content: str
    updated_at: datetime
    updated_by_user_id: UUID | None
    updated_by_email: str | None


class McpBriefingPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=0, max_length=_BRIEFING_MAX_LENGTH)


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
    settings: Settings = Depends(get_settings_dep),
    _csrf: User = Depends(require_csrf),
) -> McpBriefingResponse:
    del _csrf
    row = await _load_singleton(session)
    row.content = payload.content
    row.updated_by_user_id = actor.id
    await session.commit()
    await session.refresh(row)
    # Push the new briefing into the in-process cache the MCP server reads
    # at `initialize`. Without this, agents would keep seeing the previous
    # briefing until the next process restart — which is the kind of silent
    # staleness operators end up debugging via "did you bounce the pod".
    await refresh_cached_instructions(session, settings=settings)
    return await _serialize(session, row)
