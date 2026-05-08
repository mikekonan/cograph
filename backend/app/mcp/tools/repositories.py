from mcp.server.fastmcp import Context, FastMCP
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


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.repositories",
        description=(
            "List repositories readable by the authenticated MCP user. Returns "
            "compound slugs (host/owner/name) and graph/wiki resource URIs.\n"
            "Use when: target repo is unknown — start here to enumerate, then "
            "feed the slug into the other tools.\n"
            "Do NOT use to read a repo's docs (use cograph.repository_readme) "
            "or to search inside a repo (use cograph.retrieve / cograph.search_code)."
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
