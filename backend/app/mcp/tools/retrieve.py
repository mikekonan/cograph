from datetime import datetime
from typing import Literal
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    mcp_query_log_scope,
    resolve_readable_repository_by_slug,
    retrieve_payload,
)
from backend.app.rag.context_builder import RetrievalLayer
from backend.app.rag.snippet import (
    DEFAULT_SNIPPET_CHARS,
    MAX_SNIPPET_CHARS,
    MIN_SNIPPET_CHARS,
)

RetrieveMode = Literal["code", "wiki", "mixed"]

_MODE_TO_LAYERS: dict[RetrieveMode, set[RetrievalLayer]] = {
    "code": {RetrievalLayer.CODE, RetrievalLayer.AST, RetrievalLayer.AST_SUMMARY},
    "wiki": {RetrievalLayer.REPO_DOC},
    "mixed": set(RetrievalLayer),
}

_RETRIEVE_DESCRIPTION = (
    "Hybrid search across code, AST summaries, and repo docs. "
    "Returns query-anchored excerpts with citations and a "
    "`total_tokens_estimate` so the agent can self-budget.\n"
    "Use when: the user asks a natural-language question and needs "
    "file-anchored snippets back. Pick mode='code' for "
    "'where is X implemented', mode='wiki' for 'what is the auth flow about', "
    "mode='mixed' only when the target is unclear.\n"
    "Do NOT use for symbol-exact lookups (use cograph.search_code) or "
    "to read a known node fully (use cograph.read_node)."
)


class RetrieveToolArgs(BaseModel):
    query: str = Field(min_length=1)
    repository: str | None = None
    mode: RetrieveMode = "mixed"
    stores: list[RetrievalLayer] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
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


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.retrieve",
        description=_RETRIEVE_DESCRIPTION,
    )
    async def retrieve(
        query: str,
        repository: str | None = None,
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
        args = RetrieveToolArgs(
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
        repository_id: UUID | None = None
        current_user = current_user_from_context(ctx)
        if args.repository is not None:
            async with services.session_manager.session() as session:
                repo = await resolve_readable_repository_by_slug(
                    session=session,
                    slug=args.repository,
                    services=services,
                    current_user=current_user,
                )
            repository_id = repo.id
        async with mcp_query_log_scope(
            ctx=ctx,
            tool_name="cograph.retrieve",
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
            )
            log_bucket["result_count"] = len(getattr(response, "chunks", None) or [])
            return encode_payload(response)
