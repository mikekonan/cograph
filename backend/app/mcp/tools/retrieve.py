from datetime import datetime
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    resolve_readable_repository_by_slug,
    retrieve_payload,
)
from backend.app.rag.context_builder import RetrievalLayer


class RetrieveToolArgs(BaseModel):
    query: str = Field(min_length=1)
    repository: str | None = None
    stores: list[RetrievalLayer] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
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
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since must be earlier than or equal to until")
        if self.since is not None and self.as_of is not None and self.since > self.as_of:
            raise ValueError("since must be earlier than or equal to as_of")
        return self


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.retrieve",
        description=(
            "Hybrid retrieval across code, AST summaries, and repo docs. "
            "The optional `repository` argument is the compound slug "
            "'host/owner/name', e.g. 'github.com/mikekonan/cograph'; omit it "
            "to search across every readable repository."
        ),
    )
    async def retrieve(
        query: str,
        repository: str | None = None,
        stores: list[RetrievalLayer] | None = None,
        top_k: int = 10,
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
            stores=stores,
            top_k=top_k,
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
        response = await retrieve_payload(
            services=services,
            query=args.query,
            repository_id=repository_id,
            requested_layers=set(args.stores) if args.stores else set(RetrievalLayer),
            top_k=args.top_k,
            as_of=args.as_of,
            since=args.since,
            until=args.until,
            include_chunks=args.include_chunks,
            include_graph=args.include_graph,
            include_scores=args.include_scores,
            current_user=current_user,
        )
        return encode_payload(response)
