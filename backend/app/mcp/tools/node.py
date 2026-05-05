from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from backend.app.mcp.services import (
    MCPServices,
    encode_payload,
    node_payload,
    resolve_repository_by_slug,
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
    async def node(repository: str, node_id: UUID) -> object:
        args = NodeToolArgs(repository=repository, node_id=node_id)
        async with services.session_manager.session() as session:
            repo = await resolve_repository_by_slug(
                session=session, slug=args.repository
            )
        response = await node_payload(
            services=services,
            repository_id=repo.id,
            node_id=args.node_id,
        )
        return encode_payload(response)
