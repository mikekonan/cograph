from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    node_payload,
    resolve_readable_repository_by_slug,
)
from backend.app.rag.snippet import (
    DEFAULT_SNIPPET_CHARS,
    MAX_SNIPPET_CHARS,
    MIN_SNIPPET_CHARS,
)

_READ_NODE_DESCRIPTION = (
    "Read one code node fully (body + AST citation). Returns the snippet "
    "with `content_truncated` so the agent knows whether to widen "
    "snippet_chars.\n"
    "Use when: you have a known node_id (typically from cograph.search_code "
    "or cograph.retrieve) and need the actual code, not a list of hits.\n"
    "Do NOT use to search by name (use cograph.search_code) or to explore "
    "neighbours by graph (use cograph.related). Pass with_graph=true / "
    "with_summary=true / with_linked_docs=true only when you actually "
    "need the heavier payload — defaults skip them to keep tokens bounded."
)


class ReadNodeArgs(BaseModel):
    repository: str
    node_id: UUID
    with_graph: bool = False
    with_summary: bool = False
    with_linked_docs: bool = False
    snippet_chars: int = Field(
        default=DEFAULT_SNIPPET_CHARS,
        ge=MIN_SNIPPET_CHARS,
        le=MAX_SNIPPET_CHARS,
    )


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.read_node",
        description=_READ_NODE_DESCRIPTION,
    )
    async def read_node(
        repository: str,
        node_id: UUID,
        with_graph: bool = False,
        with_summary: bool = False,
        with_linked_docs: bool = False,
        snippet_chars: int = DEFAULT_SNIPPET_CHARS,
        ctx: Context | None = None,
    ) -> object:
        args = ReadNodeArgs(
            repository=repository,
            node_id=node_id,
            with_graph=with_graph,
            with_summary=with_summary,
            with_linked_docs=with_linked_docs,
            snippet_chars=snippet_chars,
        )
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
            with_graph=args.with_graph,
            with_summary=args.with_summary,
            with_linked_docs=args.with_linked_docs,
            snippet_chars=args.snippet_chars,
        )
        return encode_payload(response)
