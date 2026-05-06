from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from backend.app.mcp.services import (
    MCPServices,
    current_user_from_context,
    encode_payload,
    resolve_readable_repository_by_slug,
    search_code_payload,
)


class SearchCodeToolArgs(BaseModel):
    repository: str
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.search_code",
        description=(
            "Lexical and fuzzy symbol search over repository code nodes. The "
            "`repository` argument is the compound slug 'host/owner/name', "
            "e.g. 'github.com/mikekonan/cograph'."
        ),
    )
    async def search_code(
        repository: str,
        query: str,
        top_k: int = 10,
        ctx: Context | None = None,
    ) -> object:
        args = SearchCodeToolArgs(
            repository=repository,
            query=query,
            top_k=top_k,
        )
        current_user = current_user_from_context(ctx)
        async with services.session_manager.session() as session:
            repo = await resolve_readable_repository_by_slug(
                session=session,
                slug=args.repository,
                services=services,
                current_user=current_user,
            )
        response = await search_code_payload(
            services=services,
            repository_id=repo.id,
            query=args.query,
            top_k=args.top_k,
        )
        return encode_payload(response)
