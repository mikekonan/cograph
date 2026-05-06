from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from backend.app.mcp.services import (
    MCPServices,
    collection_document_payload,
    collection_search_payload,
    collections_payload,
    current_user_from_context,
    encode_payload,
)


class CollectionsToolArgs(BaseModel):
    search: str | None = None
    limit: int = Field(default=100, ge=1, le=100)


class CollectionDocumentToolArgs(BaseModel):
    collection_id: UUID
    document_id: UUID


class CollectionSearchToolArgs(BaseModel):
    collection_id: UUID
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
        name="cograph.collections",
        description=(
            "List markdown collections readable by the authenticated MCP user."
        ),
    )
    async def collections(
        search: str | None = None,
        limit: int = 100,
        ctx: Context | None = None,
    ) -> object:
        args = CollectionsToolArgs(search=search, limit=limit)
        response = await collections_payload(
            services=services,
            current_user=current_user_from_context(ctx),
            search=args.search,
            limit=args.limit,
        )
        return encode_payload(response)

    @server.tool(
        name="cograph.collection_document",
        description=(
            "Read one markdown collection document with content and parsed "
            "metadata. Requires `collection_id` and `document_id`."
        ),
    )
    async def collection_document(
        collection_id: UUID,
        document_id: UUID,
        ctx: Context | None = None,
    ) -> object:
        args = CollectionDocumentToolArgs(
            collection_id=collection_id,
            document_id=document_id,
        )
        response = await collection_document_payload(
            services=services,
            current_user=current_user_from_context(ctx),
            collection_id=args.collection_id,
            document_id=args.document_id,
        )
        return encode_payload(response)

    @server.tool(
        name="cograph.collection_search",
        description=(
            "Search chunks inside one readable markdown collection. Requires "
            "`collection_id` and a natural-language `query`."
        ),
    )
    async def collection_search(
        collection_id: UUID,
        query: str,
        top_k: int = 10,
        ctx: Context | None = None,
    ) -> object:
        args = CollectionSearchToolArgs(
            collection_id=collection_id,
            query=query,
            top_k=top_k,
        )
        response = await collection_search_payload(
            services=services,
            current_user=current_user_from_context(ctx),
            collection_id=args.collection_id,
            query=args.query,
            top_k=args.top_k,
        )
        return encode_payload(response)
