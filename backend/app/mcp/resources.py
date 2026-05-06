from uuid import UUID

from mcp.server.fastmcp import FastMCP

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    graph_node_resource_payload,
    graph_resource_payload,
    resolve_readable_repository_by_slug,
    wiki_page_resource_payload,
    wiki_tree_resource_payload,
)


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
