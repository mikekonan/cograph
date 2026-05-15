from uuid import UUID

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from backend.app.mcp.instructions import DEFAULT_BRIEFING
from backend.app.mcp.services import (
    MCPServices,
    collections_payload,
    current_user_from_context,
    graph_node_resource_payload,
    graph_resource_payload,
    repositories_payload,
    resolve_readable_repository_by_slug,
    wiki_page_resource_payload,
    wiki_tree_resource_payload,
)
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing


def register_resources(server: FastMCP, services: MCPServices) -> None:
    @server.resource(
        "cograph://repo/{host}/{owner}/{name}/graph",
        name="cograph.graph",
        title="Repository graph snapshot",
        description="Read-only symbol graph snapshot for a ready repository.",
        mime_type="application/json",
    )
    async def repository_graph(
        host: str,
        owner: str,
        name: str,
    ) -> object:
        current_user = current_user_from_context(None)
        async with services.session_manager.session() as session:
            repository = await resolve_readable_repository_by_slug(
                session=session,
                slug=f"{host}/{owner}/{name}",
                services=services,
                current_user=current_user,
            )
        return await graph_resource_payload(
            services=services,
            repository=repository,
        )

    @server.resource(
        "cograph://repo/{host}/{owner}/{name}/wiki",
        name="cograph.wiki_tree",
        title="Repository wiki tree",
        description="Generated wiki tree for a ready repository.",
        mime_type="application/json",
    )
    async def repository_wiki_tree(
        host: str,
        owner: str,
        name: str,
    ) -> object:
        current_user = current_user_from_context(None)
        async with services.session_manager.session() as session:
            repository = await resolve_readable_repository_by_slug(
                session=session,
                slug=f"{host}/{owner}/{name}",
                services=services,
                current_user=current_user,
            )
        return await wiki_tree_resource_payload(
            services=services,
            repository=repository,
        )

    @server.resource(
        "cograph://repo/{host}/{owner}/{name}/wiki/{slug}",
        name="cograph.wiki_page",
        title="Repository wiki page",
        description="Single generated wiki page with citations and related nodes.",
        mime_type="application/json",
    )
    async def repository_wiki_page(
        host: str,
        owner: str,
        name: str,
        slug: str,
    ) -> object:
        current_user = current_user_from_context(None)
        async with services.session_manager.session() as session:
            repository = await resolve_readable_repository_by_slug(
                session=session,
                slug=f"{host}/{owner}/{name}",
                services=services,
                current_user=current_user,
            )
        return await wiki_page_resource_payload(
            services=services,
            repository=repository,
            slug=slug,
        )

    @server.resource(
        "cograph://repo/{host}/{owner}/{name}/graph/node/{node_id}",
        name="cograph.graph_node",
        title="Graph node",
        description="Single code node from the repository graph.",
        mime_type="application/json",
    )
    async def repository_graph_node(
        host: str,
        owner: str,
        name: str,
        node_id: str,
    ) -> object:
        current_user = current_user_from_context(None)
        async with services.session_manager.session() as session:
            repository = await resolve_readable_repository_by_slug(
                session=session,
                slug=f"{host}/{owner}/{name}",
                services=services,
                current_user=current_user,
            )
        return await graph_node_resource_payload(
            services=services,
            repository=repository,
            node_id=UUID(node_id),
        )

    @server.resource(
        "cograph://briefing",
        name="cograph.briefing",
        title="Deployment operator briefing",
        description=(
            "The operator-edited markdown briefing for this Cograph "
            "deployment. Re-fetch after a context compaction to recover "
            "deployment-specific vocabulary and 'ask me first' rules."
        ),
        mime_type="application/json",
    )
    async def briefing_resource() -> object:
        # No ACL gate here: the briefing is already inlined into the
        # `instructions=` payload every initialised MCP client sees. Exposing
        # the same text as a resource just makes it re-fetchable after a
        # client drops the system message — same blast radius as today.
        async with services.session_manager.session() as session:
            row = (
                await session.execute(
                    select(McpOperatorBriefing).where(McpOperatorBriefing.id == 1)
                )
            ).scalar_one_or_none()
        content = (row.content if row is not None else "") or DEFAULT_BRIEFING.strip()
        return {
            "content": content,
            "updated_at": row.updated_at.isoformat() if row is not None else None,
            "is_default": row is None or not (row.content or "").strip(),
        }

    @server.resource(
        "cograph://my-context",
        name="cograph.my_context",
        title="Caller-visible repositories and collections",
        description=(
            "Lists the repositories and markdown collections the calling "
            "MCP user can read in this Cograph deployment. Fetch this on "
            "session start so you know which slugs are valid `repository=` "
            "arguments for the other tools."
        ),
        mime_type="application/json",
    )
    async def my_context_resource() -> object:
        # ACL-filtered both sides: `repositories_payload` and
        # `collections_payload` already apply the read scope keyed off the
        # MCP-authenticated user from request context. An unauthenticated
        # caller sees only what `apply_repository_read_scope` allows
        # anonymously (which is the same behavior as the existing
        # `cograph.repositories` / `cograph.collections` tools).
        current_user = current_user_from_context(None)
        repos = await repositories_payload(
            services=services,
            current_user=current_user,
            search=None,
            status=None,
            limit=100,
        )
        collections = await collections_payload(
            services=services,
            current_user=current_user,
            search=None,
            limit=100,
        )
        return {
            "repositories": {
                "total": repos["total"],  # type: ignore[index]
                "items": [
                    {"slug": item["slug"], "status": item["status"]}
                    for item in repos["items"]  # type: ignore[index]
                ],
            },
            "collections": {
                "total": collections["total"],  # type: ignore[index]
                "items": [
                    {"id": str(item["id"]), "name": item["name"]}
                    for item in collections["items"]  # type: ignore[index]
                ],
            },
        }
