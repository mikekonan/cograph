from datetime import datetime
from typing import Literal
from uuid import UUID

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.mcp.services import (
    read_only,
    search_hint,
    MCPServices,
    count_response_results,
    current_user_from_context,
    encode_payload,
    mcp_query_log_scope,
    resolve_readable_repository_by_slug,
    retrieve_payload,
    tool_args,
)
from backend.app.rag.context_builder import BROAD_RETRIEVAL_LAYERS, RetrievalLayer
from backend.app.rag.snippet import (
    DEFAULT_SNIPPET_CHARS,
    MAX_SNIPPET_CHARS,
    MIN_SNIPPET_CHARS,
)

RetrieveMode = Literal["code", "wiki", "mixed"]

# Hard ceiling on `top_k`. A mixed retrieve fans out across layers, so a
# high top_k returns a payload that dominates the agent's context. Clamp
# (not reject) so an over-eager agent still gets a bounded answer.
MAX_TOP_K = 25

_MODE_TO_LAYERS: dict[RetrieveMode, set[RetrievalLayer]] = {
    "code": {RetrievalLayer.CODE, RetrievalLayer.AST, RetrievalLayer.AST_SUMMARY},
    "wiki": {RetrievalLayer.REPO_DOC},
    # `mixed` drops the bare AST layer (signature duplicates the CODE body
    # row for the same node) — see BROAD_RETRIEVAL_LAYERS.
    "mixed": set(BROAD_RETRIEVAL_LAYERS),
}

_RETRIEVE_DESCRIPTION = (
    "Hybrid search across code, AST summaries, and checked-in docs of one "
    "repository. Returns query-anchored excerpts with citations; an excerpt "
    "is not the whole code, so read the best one or two hits with "
    "cograph_read_node.\n"
    "`repository` is required, as host/owner/name. Unknown repository: pick "
    "one with cograph_repositories or cograph_route. `top_k` bounds the "
    "results (clamped to 25); the default 10 is usually enough.\n"
    "Use when: a question in words, one topic per call. mode='code' for "
    "'where is X implemented'. mode='wiki' searches the repository's "
    "checked-in docs (README, docs/), not the generated wiki. mode='mixed' "
    "when unsure.\n"
    "Do NOT use for a name from the code (use cograph_search_code) or "
    "to read a known node fully (use cograph_read_node)."
)


class RetrieveToolArgs(BaseModel):
    query: str = Field(min_length=1)
    # Required: the retrieval engine validates repository scope server-side
    # (`validate_retrieval_scope`) and 422s without it. Advertising the arg
    # as optional made every repo-less call a guaranteed runtime error.
    repository: str = Field(min_length=1)
    mode: RetrieveMode = "mixed"
    stores: list[RetrievalLayer] | None = None
    # Upper bound is enforced by `_clamp_top_k` (clamp, not 422).
    top_k: int = Field(default=10, ge=1)
    snippet_chars: int = Field(
        default=DEFAULT_SNIPPET_CHARS,
        ge=MIN_SNIPPET_CHARS,
        le=MAX_SNIPPET_CHARS,
    )
    as_of: datetime | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_chunks: bool = True
    include_graph: bool = False
    include_scores: bool = False

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

    @model_validator(mode="after")
    def _validate_temporal_window(self) -> "RetrieveToolArgs":
        if (
            self.since is not None
            and self.until is not None
            and self.since > self.until
        ):
            raise ValueError("since must be earlier than or equal to until")
        if (
            self.since is not None
            and self.as_of is not None
            and self.since > self.as_of
        ):
            raise ValueError("since must be earlier than or equal to as_of")
        return self

    def resolved_layers(self) -> set[RetrievalLayer]:
        # Explicit `stores=` wins over `mode=` so power users keep precise control.
        if self.stores:
            return set(self.stores)
        return set(_MODE_TO_LAYERS[self.mode])


def register(server: MCPServer, services: MCPServices) -> None:
    @server.tool(
        name="cograph_retrieve",
        annotations=read_only("Search code and docs by meaning"),
        meta=search_hint(
            "semantic search, how does, where is implemented, "
            "find code by meaning, explain"
        ),
        description=_RETRIEVE_DESCRIPTION,
    )
    async def retrieve(
        query: str,
        repository: str,
        mode: RetrieveMode = "mixed",
        stores: list[RetrievalLayer] | None = None,
        top_k: int = 10,
        snippet_chars: int = DEFAULT_SNIPPET_CHARS,
        as_of: datetime | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        include_chunks: bool = True,
        include_graph: bool = False,
        include_scores: bool = False,
        ctx: Context | None = None,
    ) -> object:
        args = tool_args(
            RetrieveToolArgs,
            query=query,
            repository=repository,
            mode=mode,
            stores=stores,
            top_k=top_k,
            snippet_chars=snippet_chars,
            as_of=as_of,
            since=since,
            until=until,
            include_chunks=include_chunks,
            include_graph=include_graph,
            include_scores=include_scores,
        )
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repo = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.repository,
                services=services,
                current_user=current_user,
            )
        repository_id: UUID = repo.id
        async with mcp_query_log_scope(
            ctx=ctx,
            tool_name="cograph_retrieve",
            query_text=args.query,
            repository_id=repository_id,
            top_k=args.top_k,
        ) as log_bucket:
            response = await retrieve_payload(
                services=services,
                query=args.query,
                repository_id=repository_id,
                requested_layers=args.resolved_layers(),
                top_k=args.top_k,
                as_of=args.as_of,
                since=args.since,
                until=args.until,
                include_chunks=args.include_chunks,
                include_graph=args.include_graph,
                include_scores=args.include_scores,
                current_user=current_user,
                snippet_chars=args.snippet_chars,
                mode=args.mode,
                usage_sink=log_bucket,
            )
            log_bucket["result_count"] = count_response_results(response)
            return encode_payload(response)
