from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.enums import QueryLogSource, QueryLogStatus
from backend.app.query_logs import enqueue_query_log

from backend.app.config import Settings
from backend.app.core.errors import ApiError
from backend.app.core.md_collection_access import (
    apply_md_collection_read_scope,
    get_readable_md_collection,
)
from backend.app.core.repository_access import (
    apply_repository_read_scope,
    get_readable_repository_by_slug,
)
from backend.app.db.session import SessionManager
from backend.app.graph.traversal import (
    GraphTraversalService,
    TraversalDirection,
    TraversalResponse,
)
from backend.app.llm.embedder import EmbedProvider
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import RepositoryStatus
from backend.app.models.md_collection import MdChunk, MdCollection, MdDocument
from backend.app.models.repository import Repository
from backend.app.models.user import User
from backend.app.rag.context_builder import (
    ContextBuilder,
    RetrievalLayer,
    RetrievalResponse,
)
from backend.app.rag.fusion import rrf_merge_streams
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.lexical import LexicalRetriever, SymbolLookup
from backend.app.rag.retriever import RetrievedChunk
from backend.app.rag.service import retrieve_composite
from backend.app.rag.snippet import (
    DEFAULT_SNIPPET_CHARS,
    extract_query_terms,
    make_snippet,
)
from backend.app.wiki import WikiQueryService


@dataclass(slots=True, kw_only=True)
class MCPServices:
    settings: Settings
    session_manager: SessionManager
    embed_provider: EmbedProvider | None
    retriever: HybridRetriever
    lexical: LexicalRetriever
    symbol: SymbolLookup
    context_builder: ContextBuilder
    graph_traversal: GraphTraversalService
    wiki_queries: WikiQueryService


def encode_payload(value: object) -> object:
    return jsonable_encoder(value)


def current_user_from_context(ctx: object | None) -> User | None:
    request_context = None
    if ctx is not None:
        try:
            request_context = getattr(ctx, "request_context", None)
        except ValueError:
            request_context = None
    if request_context is None:
        try:
            from mcp.server.lowlevel.server import request_ctx

            request_context = request_ctx.get(None)
        except LookupError:
            request_context = None
        except Exception:
            request_context = None
    request = getattr(request_context, "request", None)
    state = getattr(request, "state", None)
    actor = getattr(state, "cograph_actor", None)
    if actor is None:
        scope = getattr(request, "scope", None)
        if isinstance(scope, dict):
            state_dict = scope.get("state")
            if isinstance(state_dict, dict):
                actor = state_dict.get("cograph_actor")
    return getattr(actor, "user", None)


def _mcp_error(exc: ApiError) -> ValueError:
    return ValueError(f"{exc.code}: {exc.message}")


def _app_state_from_context(ctx: object | None) -> Any | None:
    """Reach FastAPI `app.state` through the MCP request context.

    Mirrors `current_user_from_context` — same traversal, different
    attribute. We need this to pass to `enqueue_query_log` so the
    helper can reuse the arq pool initialised at app startup instead
    of opening a fresh connection on every MCP call.
    """
    request_context = None
    if ctx is not None:
        try:
            request_context = getattr(ctx, "request_context", None)
        except ValueError:
            request_context = None
    if request_context is None:
        try:
            from mcp.server.lowlevel.server import request_ctx

            request_context = request_ctx.get(None)
        except LookupError:
            request_context = None
        except Exception:
            request_context = None
    request = getattr(request_context, "request", None)
    app = getattr(request, "app", None)
    return getattr(app, "state", None)


def _mcp_client_label(ctx: object | None) -> str | None:
    """Best-effort client identifier — MCP doesn't standardise this so
    we lift whatever the client sent in headers / clientInfo.

    Returns short label like 'Claude Desktop' or 'cursor' when known,
    else None. Logged into `query_logs.client_label` so admins can
    see which tools their team is using.
    """
    request_context = None
    if ctx is not None:
        try:
            request_context = getattr(ctx, "request_context", None)
        except ValueError:
            request_context = None
    if request_context is None:
        return None
    session = getattr(request_context, "session", None)
    client_info = getattr(session, "client_info", None) if session else None
    name = getattr(client_info, "name", None) if client_info else None
    if name:
        return str(name)[:128]
    request = getattr(request_context, "request", None)
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    ua = headers.get("user-agent") if hasattr(headers, "get") else None
    return ua[:128] if ua else None


@asynccontextmanager
async def mcp_query_log_scope(
    *,
    ctx: object | None,
    tool_name: str,
    query_text: str,
    repository_id: UUID | None = None,
    collection_id: UUID | None = None,
    top_k: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Context manager that records one query_log row per MCP tool call.

    The body mutates the bucket dict to communicate measurements
    back to the recorder. Recognised keys:

    - `result_count`     — int | None, drives status=EMPTY vs OK.
    - `tokens_input`     — int | None, embed prompt tokens (+ any
                            completion input). Sum across calls if a
                            single tool dispatches more than one.
    - `tokens_output`    — int | None, completion tokens only — left
                            None for embed-only retrieval paths.
    - `embed_model`      — str | None, model id of the embedding call
                            (for pricing lookup + UI breakdown).
    - `completion_model` — str | None, model id of any completion / rerank
                            call attached to the tool (None for pure
                            retrieval). Pricing is summed across models.

    Status auto-derives from exceptions raised inside the `with`, and
    the row is enqueued in the `finally` so duration is always recorded.
    """
    started = time.perf_counter()
    bucket: dict[str, Any] = {
        "result_count": None,
        "tokens_input": None,
        "tokens_output": None,
        "embed_model": None,
        "completion_model": None,
    }
    status = QueryLogStatus.ERROR
    error_code: str | None = None
    try:
        yield bucket
        rc = bucket.get("result_count")
        status = QueryLogStatus.OK if (rc is None or rc > 0) else QueryLogStatus.EMPTY
        if rc is None:
            # No measurable count — treat as OK rather than EMPTY so
            # admin's "zero results" filter doesn't fill with these.
            status = QueryLogStatus.OK
    except Exception as exc:
        error_code = type(exc).__name__
        raise
    finally:
        user = current_user_from_context(ctx)
        if user is not None:
            await enqueue_query_log(
                app_state=_app_state_from_context(ctx),
                user_id=user.id,
                user_email=user.email,
                source=QueryLogSource.MCP,
                tool_name=tool_name,
                query_text=query_text,
                repository_id=repository_id,
                collection_id=collection_id,
                top_k=top_k,
                result_count=bucket.get("result_count"),
                duration_ms=int((time.perf_counter() - started) * 1000),
                status=status,
                error_code=error_code,
                client_label=_mcp_client_label(ctx),
                tokens_input=bucket.get("tokens_input"),
                tokens_output=bucket.get("tokens_output"),
                embed_model=bucket.get("embed_model"),
                completion_model=bucket.get("completion_model"),
            )


def count_response_results(response: object) -> int | None:
    """Best-effort `result_count` for a retrieval-shaped response.

    Handles both `RetrievalResponse`-style Pydantic models (attribute
    `.results`) and dict payloads (key `"results"`) — the two shapes our
    MCP tools and the REST `/api/retrieve` happen to return today.
    Returns `None` if the payload exposes neither, so the query log
    keeps the column nullable instead of falsely recording 0.
    """
    if response is None:
        return None
    results = getattr(response, "results", None)
    if results is None and isinstance(response, dict):
        results = response.get("results")
    if results is None:
        return None
    try:
        return len(results)
    except TypeError:
        return None


async def retrieve_payload(
    *,
    services: MCPServices,
    query: str,
    repository_id: UUID | None,
    requested_layers: set[RetrievalLayer],
    top_k: int,
    as_of: datetime | None,
    since: datetime | None,
    until: datetime | None,
    include_chunks: bool,
    include_graph: bool,
    include_scores: bool,
    current_user: User | None,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    mode: str | None = None,
    usage_sink: dict | None = None,
) -> RetrievalResponse:
    async with services.session_manager.session() as session:
        embed_provider = services.embed_provider
        if embed_provider is None:
            runtime_providers = await build_runtime_providers(
                session=session,
                settings=services.settings,
            )
            embed_provider = runtime_providers.embed_provider
        return await retrieve_composite(
            session,
            query=query,
            repository_id=repository_id,
            requested_layers=requested_layers,
            top_k=top_k,
            as_of=as_of,
            since=since,
            until=until,
            include_chunks=include_chunks,
            include_graph=include_graph,
            include_scores=include_scores,
            embed_provider=embed_provider,
            retriever=services.retriever,
            context_builder=services.context_builder,
            snippet_chars=snippet_chars,
            mode=mode,
            usage_sink=usage_sink,
        )


async def node_payload(
    *,
    services: MCPServices,
    repository_id: UUID,
    node_id: UUID,
    with_graph: bool = False,
    with_summary: bool = False,
    with_linked_docs: bool = False,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> RetrievalResponse:
    async with services.session_manager.session() as session:
        await require_ready_repository(session=session, repository_id=repository_id)
        node = await session.scalar(
            select(CodeNode).where(
                CodeNode.repository_id == repository_id,
                CodeNode.id == node_id,
            )
        )
        if node is None:
            raise ValueError("NOT_FOUND: Graph node not found")

        chunk = RetrievedChunk(
            store="code",
            chunk_id=node.id,
            content=node.content,
            score=1.0,
            metadata={
                "qualified_name": node.qualified_name,
                "file_path": node.file_path,
                "start_line": node.start_line,
                "end_line": node.end_line,
            },
        )
        requested_layers: set[RetrievalLayer] = {
            RetrievalLayer.CODE,
            RetrievalLayer.AST,
        }
        if with_summary:
            requested_layers.add(RetrievalLayer.AST_SUMMARY)
        return await services.context_builder.build(
            session,
            chunks=[chunk],
            requested_layers=requested_layers,
            repository_id=repository_id,
            include_chunks=with_linked_docs,
            include_graph=with_graph,
            include_scores=False,
            snippet_chars=snippet_chars,
        )


async def search_code_payload(
    *,
    services: MCPServices,
    repository_id: UUID,
    query: str,
    top_k: int,
) -> RetrievalResponse:
    async with services.session_manager.session() as session:
        await require_ready_repository(session=session, repository_id=repository_id)
        lexical_hits = await services.lexical.search(
            session,
            store="code",
            query_text=query,
            repository_id=repository_id,
            top_k=top_k,
        )
        symbol_hits = await services.symbol.search(
            session,
            query_text=query,
            repository_id=repository_id,
            top_k=top_k,
        )
        merged = rrf_merge_streams(
            [lexical_hits, symbol_hits],
            k=services.settings.retrieval.rrf_k,
            candidate_cap=top_k,
            stream_names=["lexical", "symbol"],
        )
        return await services.context_builder.build(
            session,
            chunks=merged[:top_k],
            requested_layers={RetrievalLayer.AST},
            repository_id=repository_id,
            include_chunks=False,
            include_graph=False,
            include_scores=True,
        )


# Hard ceiling on nodes returned by `cograph_related`. Without it a BFS
# from a hub node (depth>1, direction=both) can return thousands of nodes
# and blow the calling agent's context. 50 neighbours is plenty to spot
# the orchestration layer; anything bigger should be a targeted
# search/read, not a wider dump.
RELATED_MAX_NODES = 50


async def related_payload(
    *,
    services: MCPServices,
    repository_id: UUID,
    node_id: UUID,
    depth: int,
    direction: TraversalDirection,
) -> TraversalResponse:
    async with services.session_manager.session() as session:
        await require_ready_repository(session=session, repository_id=repository_id)
        result = await services.graph_traversal.traverse(
            session=session,
            repository_id=repository_id,
            node_id=node_id,
            depth=depth,
            direction=direction,
            max_nodes=RELATED_MAX_NODES,
        )
        if result is None:
            raise ValueError("NOT_FOUND: Graph node not found")
        return result


async def repositories_payload(
    *,
    services: MCPServices,
    current_user: User | None,
    search: str | None,
    status: RepositoryStatus | None,
    limit: int,
) -> object:
    async with services.session_manager.session() as session:
        query = apply_repository_read_scope(
            select(Repository),
            settings=services.settings,
            current_user=current_user,
        )
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            query = query.where(
                (Repository.host.ilike(pattern))
                | (Repository.owner.ilike(pattern))
                | (Repository.name.ilike(pattern))
                | (Repository.git_url.ilike(pattern))
            )
        if status is not None:
            query = query.where(Repository.status == status)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (
            await session.scalars(
                query.order_by(
                    Repository.updated_at.desc(), Repository.id.desc()
                ).limit(limit)
            )
        ).all()
        return {
            "total": total or 0,
            "limit": limit,
            "items": [
                {
                    "id": repository.id,
                    "slug": f"{repository.host}/{repository.owner}/{repository.name}",
                    "host": repository.host,
                    "owner": repository.owner,
                    "name": repository.name,
                    "branch": repository.branch,
                    "status": repository.status.value,
                    "visibility": repository.visibility.value,
                    "resources": _wiki_resource_uris(repository=repository),
                }
                for repository in rows
            ],
        }


async def collections_payload(
    *,
    services: MCPServices,
    current_user: User | None,
    search: str | None,
    limit: int,
) -> object:
    async with services.session_manager.session() as session:
        query = apply_md_collection_read_scope(
            select(MdCollection),
            current_user=current_user,
        )
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            query = query.where(
                (MdCollection.name.ilike(pattern))
                | (MdCollection.description.ilike(pattern))
            )
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (
            await session.scalars(
                query.order_by(
                    MdCollection.updated_at.desc(), MdCollection.id.desc()
                ).limit(limit)
            )
        ).all()
        collection_ids = [collection.id for collection in rows]
        doc_counts: dict[UUID, int] = {}
        if collection_ids:
            count_rows = await session.execute(
                select(MdDocument.collection_id, func.count(MdDocument.id))
                .where(MdDocument.collection_id.in_(collection_ids))
                .group_by(MdDocument.collection_id)
            )
            doc_counts = {
                collection_id: int(count) for collection_id, count in count_rows.all()
            }
        return {
            "total": total or 0,
            "limit": limit,
            "items": [
                {
                    "id": collection.id,
                    "name": collection.name,
                    "description": collection.description,
                    "visibility": collection.visibility.value,
                    "owner_id": collection.owner_id,
                    "document_count": doc_counts.get(collection.id, 0),
                }
                for collection in rows
            ],
        }


async def collection_document_payload(
    *,
    services: MCPServices,
    current_user: User | None,
    collection_id: UUID,
    document_id: UUID,
) -> object:
    async with services.session_manager.session() as session:
        try:
            collection = await get_readable_md_collection(
                session=session,
                collection_id=collection_id,
                current_user=current_user,
            )
        except ApiError as exc:
            raise _mcp_error(exc) from exc
        document = await session.scalar(
            select(MdDocument).where(
                MdDocument.collection_id == collection.id,
                MdDocument.id == document_id,
            )
        )
        if document is None:
            raise ValueError("NOT_FOUND: Document not found")
        chunk_count = (
            await session.scalar(
                select(func.count(MdChunk.id)).where(MdChunk.document_id == document.id)
            )
            or 0
        )
        return {
            "id": document.id,
            "collection_id": document.collection_id,
            "collection_name": collection.name,
            "source_key": document.source_key,
            "title": document.title,
            "content": document.content,
            "bytes": document.bytes,
            "word_count": document.word_count,
            "line_count": document.line_count,
            "frontmatter": document.frontmatter,
            "heading_tree": document.heading_tree,
            "code_blocks": document.code_blocks,
            "tables": document.tables,
            "links": document.links,
            "chunk_count": chunk_count,
            "created_at": document.created_at,
            "updated_at": document.updated_at,
        }


async def collection_search_payload(
    *,
    services: MCPServices,
    current_user: User | None,
    collection_id: UUID,
    query: str,
    top_k: int,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    usage_sink: dict | None = None,
) -> object:
    async with services.session_manager.session() as session:
        try:
            collection = await get_readable_md_collection(
                session=session,
                collection_id=collection_id,
                current_user=current_user,
            )
        except ApiError as exc:
            raise _mcp_error(exc) from exc
        embed_provider = services.embed_provider
        if embed_provider is None:
            runtime_providers = await build_runtime_providers(
                session=session,
                settings=services.settings,
            )
            embed_provider = runtime_providers.embed_provider
        if hasattr(embed_provider, "embed_with_usage"):
            vectors, embed_usage = await embed_provider.embed_with_usage([query])
            query_embedding = vectors[0]
            if usage_sink is not None:
                usage_sink["embed_model"] = embed_usage.model
                usage_sink["tokens_input"] = int(
                    (usage_sink.get("tokens_input") or 0) + embed_usage.tokens_input
                )
        else:
            query_embedding = (await embed_provider.embed([query]))[0]
        chunks = await services.retriever.retrieve(
            session,
            query_text=query,
            query_embedding=query_embedding,
            collection_id=collection.id,
            top_k=top_k,
            stores={"md_collections"},
        )
        terms = extract_query_terms(query)
        results: list[dict[str, object]] = []
        for chunk in chunks:
            snippet, truncated = make_snippet(chunk.content, terms, chars=snippet_chars)
            results.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.metadata.get("document_id"),
                    "source_key": chunk.metadata.get("source_key", ""),
                    "title": chunk.metadata.get("title"),
                    "heading_path": chunk.metadata.get("heading_path", []),
                    "snippet": snippet,
                    "content_truncated": truncated,
                    "score": chunk.score,
                    "vector_rank": chunk.metadata.get("vector_rank"),
                    "lexical_rank": chunk.metadata.get("lexical_rank"),
                    "rerank_score": chunk.metadata.get("rerank_score"),
                }
            )
        total_tokens_estimate = (
            sum(len(str(r.get("snippet") or "")) for r in results) // 4
        )
        return {
            "collection_id": collection.id,
            "collection_name": collection.name,
            "query": query,
            "results": results,
            "total_tokens_estimate": total_tokens_estimate,
        }


async def read_chunk_payload(
    *,
    services: MCPServices,
    current_user: User | None,
    collection_id: UUID,
    chunk_id: UUID,
) -> object:
    async with services.session_manager.session() as session:
        try:
            collection = await get_readable_md_collection(
                session=session,
                collection_id=collection_id,
                current_user=current_user,
            )
        except ApiError as exc:
            raise _mcp_error(exc) from exc
        chunk = await session.scalar(
            select(MdChunk)
            .join(MdDocument, MdDocument.id == MdChunk.document_id)
            .where(
                MdChunk.id == chunk_id,
                MdDocument.collection_id == collection.id,
            )
        )
        if chunk is None:
            raise ValueError("NOT_FOUND: Chunk not found")
        document = await session.get(MdDocument, chunk.document_id)
        return {
            "collection_id": collection.id,
            "collection_name": collection.name,
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "heading_path": chunk.heading_path,
            "content": chunk.content,
            "source_key": document.source_key if document is not None else None,
            "title": document.title if document is not None else None,
        }


async def wiki_tree_resource_payload(
    *,
    services: MCPServices,
    repository: Repository,
) -> object:
    async with services.session_manager.session() as session:
        await require_ready_repository(
            session=session,
            repository_id=repository.id,
        )
        tree = await services.wiki_queries.list_tree(
            session=session,
            repository_id=repository.id,
        )
        total = await services.wiki_queries.count_pages(
            session=session,
            repository_id=repository.id,
        )
        compact = await services.wiki_queries.get_compact(
            session=session,
            repository_id=repository.id,
        )
        return encode_payload(
            {
                "repository_id": repository.id,
                "host": repository.host,
                "owner": repository.owner,
                "name": repository.name,
                "resources": _wiki_resource_uris(repository=repository),
                "items": tree,
                "total": total,
                # The compacted whole-wiki map (~2-3k tokens): every page's
                # lead prose, section headings, and covered questions. This is
                # the ONLY form of the generated wiki served over MCP — full
                # page bodies are deliberately not reachable from agents.
                "compact": compact,
            }
        )


def _wiki_resource_uris(
    *,
    repository: Repository,
) -> dict[str, str]:
    # Deliberately no per-page URI (the compact map is the only form of the
    # generated wiki served over MCP) and no graph URI (the whole-repo graph
    # snapshot was a 40-60k-token dump; targeted tools — search_code,
    # retrieve, read_node, related — cover every agent need).
    slug_path = f"{repository.host}/{repository.owner}/{repository.name}"
    return {
        "tree": f"cograph://repo/{slug_path}/wiki",
    }


async def require_ready_repository(
    *,
    session: AsyncSession,
    repository_id: UUID,
) -> Repository:
    repository = await require_repository(
        session=session,
        repository_id=repository_id,
    )
    if repository.status is not RepositoryStatus.READY:
        raise ValueError("REPO_NOT_READY: Repository is not ready")
    return repository


async def require_repository(
    *,
    session: AsyncSession,
    repository_id: UUID,
) -> Repository:
    repository = await session.get(Repository, repository_id)
    if repository is None:
        raise ValueError("NOT_FOUND: Repository not found")
    return repository


async def resolve_repository_by_slug(
    *,
    session: AsyncSession,
    slug: str,
) -> Repository:
    """Resolve a Repository row from a `host/owner/name` slug.

    Used by every MCP tool that takes a `repository` arg. Raises
    `ValueError("NOT_FOUND: ...")` for missing rows or malformed slugs to
    mirror the MCP convention used by `require_repository`.
    """
    parts = [segment for segment in slug.strip().split("/") if segment]
    if len(parts) != 3:
        raise ValueError(
            "NOT_FOUND: repository slug must be of the form 'host/owner/name'"
        )
    host, owner, name = parts
    repository = await session.scalar(
        select(Repository).where(
            Repository.host == host,
            Repository.owner == owner,
            Repository.name == name,
            Repository.deleted_at.is_(None),
        )
    )
    if repository is None:
        raise ValueError("NOT_FOUND: Repository not found")
    return repository


async def resolve_readable_repository_by_slug(
    *,
    session: AsyncSession,
    slug: str,
    services: MCPServices,
    current_user: User | None,
) -> Repository:
    parts = [segment for segment in slug.strip().split("/") if segment]
    if len(parts) != 3:
        raise ValueError(
            "NOT_FOUND: repository slug must be of the form 'host/owner/name'"
        )
    host, owner, name = parts
    try:
        return await get_readable_repository_by_slug(
            session=session,
            host=host,
            owner=owner,
            name=name,
            settings=services.settings,
            current_user=current_user,
        )
    except ApiError as exc:
        raise _mcp_error(exc) from exc
