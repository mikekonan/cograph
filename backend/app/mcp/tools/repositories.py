from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field, field_validator

from backend.app.mcp.services import (
    read_only,
    search_hint,
    NOT_READABLE_REPOSITORY,
    MCPServices,
    current_user_from_context,
    encode_payload,
    repositories_payload,
    tool_args,
)
from backend.app.models.enums import RepositoryStatus
from backend.app.rag.source_router import route_sources


class RepositoriesToolArgs(BaseModel):
    search: str | None = None
    status: RepositoryStatus | None = None
    # Clamped, not rejected: agents ask for 200 and got an error instead of a list.
    limit: int = Field(default=100, ge=1)

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        return min(value, 100)


def register(server: MCPServer, services: MCPServices) -> None:
    @server.tool(
        name="cograph_repositories",
        annotations=read_only("List repositories"),
        meta=search_hint("which repository, find repo, list repos, where does it live"),
        description=(
            "List repositories readable by the authenticated MCP user. Returns "
            "compound slugs (host/owner/name) and the wiki resource URI. There "
            "is no graph resource URI — use cograph_search_code / "
            "cograph_read_node / cograph_related for the graph.\n"
            "Use when: you know a repository only by name (pass "
            "search=name) or need the list; feed the host/owner/name slug "
            "into the other tools.\n"
            "Do NOT use to read a repo's docs (use cograph_repository_readme) "
            "or to search inside a repo (use cograph_retrieve / cograph_search_code)."
        ),
    )
    async def repositories(
        search: str | None = None,
        status: RepositoryStatus | None = None,
        limit: int = 100,
        ctx: Context | None = None,
    ) -> object:
        args = tool_args(RepositoriesToolArgs, search=search, status=status, limit=limit)
        current_user = current_user_from_context(ctx)
        response = await repositories_payload(
            services=services,
            current_user=current_user,
            search=args.search,
            status=args.status,
            limit=args.limit,
        )
        if not response["items"] and args.search:
            # Agents search by a service's name, and the service often lives in
            # a repository with another name. Without candidates they reported
            # "the code is not in Cograph" for code that was there.
            async with services.session_manager.session() as session:
                hits = await route_sources(
                    session,
                    query=args.search[:512],
                    current_user=current_user,
                    settings=services.settings,
                )
            candidates = [
                {"repository": hit.label, "why": hit.why}
                for hit in hits
                if hit.kind == "repository"
            ]
            if candidates:
                response["candidates"] = candidates
                response["reason"] = (
                    f"No repository you can read is named '{args.search}'. A service "
                    "often lives in a repository with another name. `candidates` are "
                    "repositories whose name, README or code mention it: search them "
                    "before you say the code is not in Cograph."
                )
            else:
                response["reason"] = NOT_READABLE_REPOSITORY.format(f"matching '{args.search}'")
        return encode_payload(response)
