from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from backend.app.graph.traversal import TraversalDirection
from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    related_payload,
    resolve_readable_repository_by_slug,
)


class RelatedToolArgs(BaseModel):
    repository: str
    node_id: UUID
    depth: int = Field(default=1, ge=1, le=5)
    direction: TraversalDirection = TraversalDirection.BOTH


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.related",
        description=(
            "Traverse callers and callees around a code node. The "
            "`repository` argument is the compound slug 'host/owner/name', "
            "e.g. 'github.com/mikekonan/cograph'."
        ),
    )
    async def related(
        repository: str,
        node_id: UUID,
        depth: int = 1,
        direction: TraversalDirection = TraversalDirection.BOTH,
        ctx: Context | None = None,
    ) -> object:
        args = RelatedToolArgs(
            repository=repository,
            node_id=node_id,
            depth=depth,
            direction=direction,
        )
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repo = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.repository,
                services=services,
                current_user=current_user,
            )
        response = await related_payload(
            services=services,
            repository_id=repo.id,
            node_id=args.node_id,
            depth=args.depth,
            direction=args.direction,
        )
        return encode_payload(response)
