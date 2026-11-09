import re

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field, field_validator

from backend.app.mcp.services import (
    read_only,
    search_hint,
    MCPServices,
    count_response_results,
    current_user_from_context,
    encode_payload,
    mcp_query_log_scope,
    resolve_readable_repository_by_slug,
    search_code_payload,
    tool_args,
)
from backend.app.mcp.tools.retrieve import MAX_TOP_K


class SearchCodeToolArgs(BaseModel):
    repository: str
    query: str = Field(min_length=1)
    # Clamped like cograph_retrieve: hits past ~25 are rarely followed up,
    # yet each one costs the agent context for the rest of the session.
    top_k: int = Field(default=10, ge=1)
    path_prefix: str | None = None

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("top_k")
    @classmethod
    def _clamp_top_k(cls, value: int) -> int:
        return min(value, MAX_TOP_K)

    @field_validator("path_prefix")
    @classmethod
    def _relative_path_prefix(cls, value: str | None) -> str:
        # Stored paths are relative to the repository root: drop `./` and `/`.
        prefix = re.sub(r"^(\./|/)+", "", (value or "").strip())
        return "" if prefix == "." else prefix


def register(server: MCPServer, services: MCPServices) -> None:
    @server.tool(
        name="cograph_search_code",
        annotations=read_only("Find code by name"),
        meta=search_hint(
            "find function, class, method, type, symbol, identifier, definition"
        ),
        description=(
            "Lexical + fuzzy symbol search over a repository's code nodes. "
            "Returns signatures + line ranges (no body), at most 25 hits, so "
            "the agent can pick the right node, then read it with "
            "cograph_read_node(node_id=provenance.node_id).\n"
            "path_prefix (a folder from the repository root, e.g. "
            "internal/shipping/) keeps only hits under it.\n"
            "Use when: you have a name from the code (class, function, "
            "qualified path). One identifier per call. `repository` is "
            "host/owner/name.\n"
            "Do NOT use for natural-language questions (use cograph_retrieve "
            "with mode=code) or to read a node fully (use cograph_read_node)."
        ),
    )
    async def search_code(
        repository: str,
        query: str,
        top_k: int = 10,
        path_prefix: str | None = None,
        ctx: Context | None = None,
    ) -> object:
        args = tool_args(
            SearchCodeToolArgs,
            repository=repository,
            query=query,
            top_k=top_k,
            path_prefix=path_prefix,
        )
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repo = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.repository,
                services=services,
                current_user=current_user,
            )
        async with mcp_query_log_scope(
            ctx=ctx,
            tool_name="cograph_search_code",
            query_text=args.query,
            repository_id=repo.id,
            top_k=args.top_k,
        ) as log_bucket:
            response = await search_code_payload(
                services=services,
                repository_id=repo.id,
                query=args.query,
                top_k=args.top_k,
                path_prefix=args.path_prefix or "",
            )
            log_bucket["result_count"] = count_response_results(response)
            return encode_payload(response)
