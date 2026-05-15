"""MCP tool: `cograph.route(query, top_k=3)`.

Cheap source-routing step the playbook tells the agent to call FIRST when
the user's question doesn't name a specific repository. The agent then
fans out to the returned candidates (per the playbook's "≥0.7 take all,
else top-2" rule) rather than burning tokens on a global retrieve.

Returns top-`k` repository candidates AND top-`k` collection candidates
side by side — the agent treats them as two independent verticals. See
`backend.app.rag.source_router` for the scoring logic.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
)
from backend.app.rag.source_router import RouteHit, route_sources


_DESCRIPTION = (
    "Locate the repositories and markdown collections most likely to "
    "contain the answer to a natural-language query. Returns up to "
    "`top_k` candidates per kind with a `score` in [0, 1] and a one-line "
    "`why` explanation.\n"
    "Use when: the user's question does NOT name a specific repository "
    "or collection — call this first, then fan out to ALL high-confidence "
    "candidates (score ≥ 0.7) with cograph.outline / cograph.retrieve. "
    "Facts routinely span sources; collapsing to a single candidate is a "
    "bug.\n"
    "Do NOT use when the user already named a slug (skip straight to "
    "cograph.outline + cograph.retrieve with `repository=<slug>`), and "
    "do NOT use as a substitute for cograph.retrieve — route returns "
    "*pointers to sources*, not snippets."
)


class RouteToolArgs(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=3, ge=1, le=10)


def _hit_payload(hit: RouteHit) -> dict[str, object]:
    return {
        "kind": hit.kind,
        "id": hit.id,
        "label": hit.label,
        "score": round(hit.score, 3),
        "why": hit.why,
    }


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(name="cograph.route", description=_DESCRIPTION)
    async def route(
        query: str,
        top_k: int = 3,
        ctx: Context | None = None,
    ) -> object:
        args = RouteToolArgs(query=query, top_k=top_k)
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            hits = await route_sources(
                session,
                query=args.query,
                current_user=current_user,
                settings=services.settings,
                top_k=args.top_k,
            )
        repositories = [_hit_payload(h) for h in hits if h.kind == "repository"]
        collections = [_hit_payload(h) for h in hits if h.kind == "collection"]
        return encode_payload(
            {
                "query": args.query,
                "repositories": repositories,
                "collections": collections,
            }
        )
