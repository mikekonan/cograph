from uuid import UUID

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from backend.app.mcp.services import (
    read_only,
    search_hint,
    MCPServices,
    current_user_from_context,
    encode_payload,
    node_payload,
    resolve_readable_repository_by_slug,
    tool_args,
)
from backend.app.rag.snippet import MIN_SNIPPET_CHARS

# A node body is read as code, so it gets a larger budget than a search
# snippet. 16000 chars is ~400 lines; past that read by line range.
READ_NODE_DEFAULT_CHARS = 6000
READ_NODE_MAX_CHARS = 16000

_READ_NODE_DESCRIPTION = (
    "Read one code node's exact source (a function, method or type) with its "
    "AST citation. The body keeps its real lines from provenance.start_line, "
    "at most snippet_chars (default 6000, max 16000) cut on a line boundary; "
    "if `content_truncated`, continue with cograph_read_file_range.\n"
    "Use when: you have a node_id (from cograph_search_code, cograph_retrieve "
    "or cograph_related) and need its code. Prefer it to "
    "cograph_read_file_range for a whole function: it returns exactly the "
    "node, no guessed line window.\n"
    "Do NOT use to search by name (use cograph_search_code) or to explore "
    "neighbours by graph (use cograph_related). Pass with_graph=true / "
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
        default=READ_NODE_DEFAULT_CHARS,
        ge=MIN_SNIPPET_CHARS,
        le=READ_NODE_MAX_CHARS,
    )


def register(server: MCPServer, services: MCPServices) -> None:
    @server.tool(
        name="cograph_read_node",
        annotations=read_only("Read a code node"),
        meta=search_hint("read source, show code, function body"),
        description=_READ_NODE_DESCRIPTION,
    )
    async def read_node(
        repository: str,
        node_id: UUID,
        with_graph: bool = False,
        with_summary: bool = False,
        with_linked_docs: bool = False,
        snippet_chars: int = READ_NODE_DEFAULT_CHARS,
        ctx: Context | None = None,
    ) -> object:
        args = tool_args(
            ReadNodeArgs,
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
