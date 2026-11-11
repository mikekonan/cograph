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
from backend.app.rag.snippet import extract_query_terms


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
            "Returns at most 25 records {node_id, name, at: path:start-end, "
            "called_by, code}, where `code` is the signature, not the body. "
            "Pick the right node, then read it with cograph_read_node(node_id).\n"
            "`called_by` lists up to 3 callers {name, at}; "
            "called_by_more counts the rest; [] means no callers.\n"
            "No hits: `reason` names the words searched for.\n"
            "`wiki` (only when a generated-wiki page covers the hits) lists up "
            "to 2 pages {page, title, section, excerpt}; read one with "
            "cograph_wiki_page(page, section).\n"
            "path_prefix (a folder from the repository root, e.g. "
            "internal/shipping/) keeps only hits under it.\n"
            "Use when: you have a word from the code: a name (class, "
            "function, qualified path) or a word that appears in the code. "
            "`repository` is host/owner/name.\n"
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
            payload = await search_code_payload(
                services=services,
                repository_id=repo.id,
                query=args.query,
                top_k=args.top_k,
                path_prefix=args.path_prefix or "",
            )
            if not payload["results"]:
                # The same words the lexical stream searched for.
                words = ", ".join(extract_query_terms(args.query)) or (
                    "none, every word is too short or too common"
                )
                where = f" under path_prefix '{args.path_prefix}'" if args.path_prefix else ""
                payload["reason"] = f"No code{where} matches any of the words: {words}."
            log_bucket["result_count"] = count_response_results(payload)
            log_bucket["wiki_pages"] = [p["page"] for p in payload.get("wiki") or []]
            return encode_payload(payload)
