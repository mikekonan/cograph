from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    node_payload,
    resolve_readable_repository_by_slug,
)


class NodeToolArgs(BaseModel):
    repository: str
    node_id: UUID


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.node",
        description=(
            "Load one code node with code, AST, summary, neighbours, and "
            "linked repo docs. The `repository` argument is the compound "
            "slug 'host/owner/name', e.g. 'github.com/mikekonan/cograph'."
        ),
    )
    async def node(
        repository: str,
        node_id: UUID,
        ctx: Context | None = None,
    ) -> object:
        args = NodeToolArgs(repository=repository, node_id=node_id)
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repo = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.repository,
                services=services,
                current_user=current_user,
            )
        response = await node_payload(
            services=services,
            repository_id=repo.id,
            node_id=args.node_id,
        )
        return encode_payload(response)
