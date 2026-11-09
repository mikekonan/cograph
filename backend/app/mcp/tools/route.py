"""MCP tool: `cograph_route(query, top_k=3)`.

Optional source-routing step for a question that names no repository.
The agent searches the top one or two candidates.

Returns top-`k` repository candidates AND top-`k` collection candidates
side by side — the agent treats them as two independent verticals. See
`backend.app.rag.source_router` for the scoring logic.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from backend.app.mcp.services import (
    read_only,
    search_hint,
    MCPServices,
    current_user_from_context,
    encode_payload,
    tool_args,
)
from backend.app.rag.source_router import RouteHit, route_sources


_DESCRIPTION = (
    "Rank the repositories and markdown collections most likely to "
    "hold the answer to a natural-language query. Returns up to "
    "`top_k` candidates per kind with a rough `score` in [0, 1] and a "
    "one-line `why`.\n"
    "Use when: the question does not name a repository and you do not "
    "know which one holds the answer. Optional: skip it when you know "
    "the repository. Use the top 1-2 candidates; scores are rough.\n"
    "Do NOT use when the repository is known (go straight to "
    "cograph_search_code or cograph_retrieve), and do NOT use instead of "
    "cograph_retrieve: route returns pointers to sources, not code."
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


def register(server: MCPServer, services: MCPServices) -> None:
    @server.tool(
        name="cograph_route",
        description=_DESCRIPTION,
        annotations=read_only("Find the right repository"),
        meta=search_hint("which repository, find repo, list repos, where does it live"),
    )
    async def route(
        query: str,
        top_k: int = 3,
        ctx: Context | None = None,
    ) -> object:
        args = tool_args(RouteToolArgs, query=query, top_k=top_k)
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
