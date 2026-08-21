from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    repositories_payload,
)
from backend.app.models.enums import RepositoryStatus


class RepositoriesToolArgs(BaseModel):
    search: str | None = None
    status: RepositoryStatus | None = None
    limit: int = Field(default=100, ge=1, le=100)


def register(server: MCPServer, services: MCPServices) -> None:
    @server.tool(
        name="cograph_repositories",
        description=(
            "List repositories readable by the authenticated MCP user. Returns "
            "compound slugs (host/owner/name) and the wiki resource URI. There "
            "is no graph resource URI — use cograph_search_code / "
            "cograph_read_node / cograph_related for the graph.\n"
            "Use when: target repo is unknown — start here to enumerate, then "
            "feed the slug into the other tools.\n"
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
        args = RepositoriesToolArgs(search=search, status=status, limit=limit)
        response = await repositories_payload(
            services=services,
            current_user=current_user_from_context(ctx),
            search=args.search,
            status=args.status,
            limit=args.limit,
        )
        return encode_payload(response)
