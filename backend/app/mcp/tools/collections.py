from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from backend.app.mcp.services import (
    MCPServices,
    collection_document_payload,
    collection_search_payload,
    collections_payload,
    count_response_results,
    current_user_from_context,
    encode_payload,
    mcp_query_log_scope,
    read_chunk_payload,
)
from backend.app.rag.snippet import (
    DEFAULT_SNIPPET_CHARS,
    MAX_SNIPPET_CHARS,
    MIN_SNIPPET_CHARS,
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
    snippet_chars: int = Field(
        default=DEFAULT_SNIPPET_CHARS,
        ge=MIN_SNIPPET_CHARS,
        le=MAX_SNIPPET_CHARS,
    )

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class ReadChunkToolArgs(BaseModel):
    collection_id: UUID
    chunk_id: UUID


def register(server: FastMCP, services: MCPServices) -> None:
    @server.tool(
        name="cograph.collections",
        description=(
            "List markdown collections readable by the authenticated MCP user.\n"
            "Use when: target collection_id is unknown — start here to enumerate.\n"
            "Do NOT use to read a collection's documents (use "
            "cograph.collection_document) or to search inside a collection "
            "(use cograph.collection_search)."
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
            "Read one full markdown collection document with parsed metadata "
            "(headings, code blocks, tables, links). No truncation.\n"
            "Use when: agent has a known document_id from a prior "
            "cograph.collection_search / cograph.outline call.\n"
            "Do NOT use to find documents (use cograph.collection_search) or to "
            "read just one chunk (use cograph.read_chunk — cheaper)."
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
            "Hybrid search inside one readable markdown collection. Returns "
            "query-anchored excerpts (snippet + content_truncated) per chunk "
            "with a top-level total_tokens_estimate.\n"
            "Use when: agent has a known collection_id and a natural-language "
            "question targeting markdown content.\n"
            "Do NOT use to search code/wiki across repositories (use "
            "cograph.retrieve) or to read a chunk fully (use cograph.read_chunk)."
        ),
    )
    async def collection_search(
        collection_id: UUID,
        query: str,
        top_k: int = 10,
        snippet_chars: int = DEFAULT_SNIPPET_CHARS,
        ctx: Context | None = None,
    ) -> object:
        args = CollectionSearchToolArgs(
            collection_id=collection_id,
            query=query,
            top_k=top_k,
            snippet_chars=snippet_chars,
        )
        async with mcp_query_log_scope(
            ctx=ctx,
            tool_name="cograph.collection_search",
            query_text=args.query,
            collection_id=args.collection_id,
            top_k=args.top_k,
        ) as log_bucket:
            response = await collection_search_payload(
                services=services,
                current_user=current_user_from_context(ctx),
                collection_id=args.collection_id,
                query=args.query,
                top_k=args.top_k,
                snippet_chars=args.snippet_chars,
            )
            log_bucket["result_count"] = count_response_results(response)
            return encode_payload(response)

    @server.tool(
        name="cograph.read_chunk",
        description=(
            "Fetch the full content of one markdown collection chunk by id.\n"
            "Use when: cograph.collection_search returned a hit with "
            "content_truncated=true and the agent needs the rest of the chunk.\n"
            "Do NOT use to read whole documents (use cograph.collection_document) "
            "or chunks from a different collection — chunk_id is scoped to its "
            "collection by access check."
        ),
    )
    async def read_chunk(
        collection_id: UUID,
        chunk_id: UUID,
        ctx: Context | None = None,
    ) -> object:
        args = ReadChunkToolArgs(
            collection_id=collection_id,
            chunk_id=chunk_id,
        )
        response = await read_chunk_payload(
            services=services,
            current_user=current_user_from_context(ctx),
            collection_id=args.collection_id,
            chunk_id=args.chunk_id,
        )
        return encode_payload(response)
