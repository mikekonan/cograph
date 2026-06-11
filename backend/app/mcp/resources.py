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
    wiki_tree_resource_payload,
)
from backend.app.models.mcp_operator_briefing import McpOperatorBriefing


def register_resources(server: FastMCP, services: MCPServices) -> None:
    @server.resource(
        "cograph://repo/{host}/{owner}/{name}/graph",
        name="cograph_graph",
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
        name="cograph_wiki_tree",
        title="Repository wiki (compacted)",
        description=(
            "The generated wiki for a ready repository, served compacted: "
            "the page tree plus, per page, its lead prose, section "
            "headings, and the reader-questions it answers (~2-3k tokens for "
            "the whole wiki). This is the ONLY form of the generated wiki "
            "available over MCP — there is no full-page resource. Cite "
            "entries as `wiki/<slug>`; drill into the underlying code via "
            "retrieve/search instead of looking for longer wiki prose."
        ),
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
        "cograph://repo/{host}/{owner}/{name}/graph/node/{node_id}",
        name="cograph_graph_node",
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
        name="cograph_briefing",
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
        name="cograph_my_context",
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
        # `cograph_repositories` / `cograph_collections` tools).
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
        # Surface wiki_total per repository so the agent can see at session
        # start which repos actually have generated wiki pages — the playbook's
        # "Wiki gate" rule keys off this to decide whether reading the compact
        # wiki resource is mandatory for that repo. Cost is one COUNT(*) per repo
        # (capped at 100 by the limit above), acceptable for a session-bootstrap
        # resource that the agent fetches once.
        repo_items = []
        async with services.session_manager.session() as session:
            for item in repos["items"]:  # type: ignore[index]
                wiki_total = await services.wiki_queries.count_pages(
                    session=session,
                    repository_id=item["id"],
                )
                repo_items.append(
                    {
                        "slug": item["slug"],
                        "status": item["status"],
                        "wiki_total": wiki_total,
                    }
                )
        return {
            "repositories": {
                "total": repos["total"],  # type: ignore[index]
                "items": repo_items,
            },
            "collections": {
                "total": collections["total"],  # type: ignore[index]
                "items": [
                    {"id": str(item["id"]), "name": item["name"]}
                    for item in collections["items"]  # type: ignore[index]
                ],
            },
        }
