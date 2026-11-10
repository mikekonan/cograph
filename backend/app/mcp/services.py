from __future__ import annotations

import json
import posixpath
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, TypeVar
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError
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
from backend.app.mcp.auth import mcp_actor_var
from backend.app.graph.traversal import (
    GraphTraversalService,
    TraversalDirection,
    TraversalResponse,
)
from backend.app.llm.embedder import EmbedProvider
from backend.app.llm.runtime_providers import build_runtime_providers
from backend.app.models.code_edge import CodeEdge
from backend.app.models.code_node import CodeNode
from backend.app.models.document import Document
from backend.app.models.enums import CodeEdgeType, RepositoryStatus
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
from backend.app.wiki.compact import (
    extract_lead,
    extract_section,
    extract_sections,
    truncate_markdown,
)


def read_only(title: str) -> ToolAnnotations:
    # Every tool only reads. Clients run read-only tools in parallel; without the
    # hint they call them one by one. Clients show `title` in tool lists.
    return ToolAnnotations(title=title, readOnlyHint=True)


def search_hint(words: str) -> dict[str, str]:
    # Claude Code defers MCP tools and finds them by keyword (ToolSearch); this
    # key adds words to match on. The model never reads it.
    return {"anthropic/searchHint": words}


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


def encode_payload(value: object) -> str:
    """One tool result as compact JSON text.

    Returning text keeps the SDK from pretty-printing a dict with
    `indent=2`. Nulls and empty containers are dropped below the top level:
    unset scores, empty doc links and empty metadata were a large share of
    every search hit and told the agent nothing. Top-level keys stay, so an
    empty `results` still reads as "no hits".
    """

    encoded = jsonable_encoder(value)
    if isinstance(encoded, dict):
        encoded = {key: _drop_empty(item) for key, item in encoded.items()}
    return json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))


# An empty caller list means "looked, none": it answers the question.
_KEEP_EMPTY = frozenset({"callers", "callees", "called_by"})


def _drop_empty(value: object) -> object:
    if isinstance(value, dict):
        pairs = ((key, _drop_empty(item)) for key, item in value.items())
        return {
            key: item
            for key, item in pairs
            if key in _KEEP_EMPTY or item not in (None, [], {})
        }
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    return value


ArgsT = TypeVar("ArgsT", bound=BaseModel)


def tool_args(model: type[ArgsT], /, **fields: object) -> ArgsT:
    """Build a tool's argument model, reporting a bad argument as a tool failure.

    Every tool validates by constructing its model inside the body rather than by
    typing the parameters, so the `ValidationError` surfaces there too — and since
    mcp 2.2 an exception raised from a body is a crash: the caller gets a bare
    `Error executing tool <name>` with the offending field withheld, and the
    server logs a traceback for what is only a malformed call. Wrapping it as a
    `ToolError` restores the message an agent needs to correct itself, and keeps
    the field constraints (`ge`, `le`, `min_length`) reporting like the custom
    validators beside them.
    """

    try:
        return model(**fields)
    except ValidationError as exc:
        raise ToolError(f"INVALID_REQUEST: {exc}") from exc


def _request_context(ctx: object | None) -> object | None:
    """The MCP request context carried by a tool's `Context` argument.

    `Context.request_context` raises rather than returning None when the
    tool is called outside a request, which is why this is a try. There
    used to be a fallback onto a module-level `request_ctx` contextvar in
    the SDK's lowlevel server; the SDK no longer exposes one, and the
    `Context` argument every tool already receives is the supported way
    to reach it.
    """

    if ctx is None:
        return None
    try:
        return getattr(ctx, "request_context", None)
    except ValueError:
        return None


def current_user_from_context(ctx: object | None) -> User | None:
    request_context = _request_context(ctx)
    request = getattr(request_context, "request", None)
    state = getattr(request, "state", None)
    actor = getattr(state, "cograph_actor", None)
    if actor is None:
        scope = getattr(request, "scope", None)
        if isinstance(scope, dict):
            state_dict = scope.get("state")
            if isinstance(state_dict, dict):
                actor = state_dict.get("cograph_actor")
    if actor is None:
        # Resources get no `Context`; the auth wrapper's contextvar still has it.
        actor = mcp_actor_var.get()
    return getattr(actor, "user", None)


def _mcp_error(exc: ApiError) -> ToolError:
    """Translate an `ApiError` into the SDK's anticipated-failure exception.

    `ToolError`, not `ValueError`: since mcp 2.2 the SDK forwards the message of
    a `ToolError` to the caller and logs it at INFO, while anything else counts
    as a crash — the model is handed a bare `Error executing tool <name>` and the
    server logs a traceback at ERROR. Every code below is a condition we expect
    (a missing node, a repository the caller cannot read), so masking the code
    both strips the one thing an agent can act on and files an ordinary 404 as a
    server fault.
    """

    return ToolError(f"{exc.code}: {exc.message}")


def _app_state_from_context(ctx: object | None) -> Any | None:
    """Reach FastAPI `app.state` through the MCP request context.

    Mirrors `current_user_from_context` — same traversal, different
    attribute. We need this to pass to `enqueue_query_log` so the
    helper can reuse the arq pool initialised at app startup instead
    of opening a fresh connection on every MCP call.
    """
    request_context = _request_context(ctx)
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
    request_context = _request_context(ctx)
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
        # Prefer the domain code carried by ApiError ("VALIDATION_FAILED",
        # "EMBEDDING_PROVIDER_FAILED", ...) over the bare class name — a
        # column full of "ApiError" hides the actual cause.
        domain_code = getattr(exc, "code", None)
        if isinstance(domain_code, str) and domain_code:
            error_code = domain_code[:64]
        else:
            error_code = type(exc).__name__[:64]
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


def compact_hits(
    response: RetrievalResponse,
    *,
    with_dates: bool = False,
    with_graph: bool = False,
    called_by: dict[UUID, tuple[list[dict[str, Any]], int]] | None = None,
) -> dict[str, object]:
    """A search response as the agent reads it: one record per place.

    The builder returns one code node as up to three rows (body, signature,
    summary), each with the same citation, two commit SHAs and stream
    metadata, and repeats the docs linked to the node on every row. Here a
    node becomes one record and a doc section another, in the order each
    first appears. REST and the web UI keep the full response.

    `called_by` maps a node to its first callers and their total, from
    `_called_by`. Without it the records carry no `called_by` field: the
    graph was not looked at.
    """

    places: dict[object, dict[str, Any]] = {}
    for row in response.results:
        if row.layer is RetrievalLayer.AST_SUMMARY:
            # MCP asks for the code layer instead; summaries stay in
            # read_node and the wiki.
            continue
        cite = row.provenance
        if cite.node_id is None:
            _add_doc(places, cite.file_path, cite.heading_path, row.snippet, row.content_truncated)
            continue
        record = places.get(cite.node_id)
        if record is None:
            record = places[cite.node_id] = {
                "node_id": cite.node_id,
                "name": cite.qualified_name,
                "at": _at(cite.file_path, cite.start_line, cite.end_line),
                "score": row.score,
                "last_changed_at": cite.last_changed_at if with_dates else None,
            }
            if called_by is not None:
                callers, total = called_by.get(cite.node_id, ([], 0))
                record["called_by"] = callers
                record["called_by_more"] = total - len(callers) or None
        if row.layer is RetrievalLayer.CODE or "code" not in record:
            # The signature row stands in only for a node with no body row.
            record["code"] = row.snippet
            record["truncated"] = row.content_truncated or None
        for linked in row.related_repo_doc_chunks:
            _add_doc(places, linked.file_path, linked.heading_path, linked.snippet, False)

    records = list(places.values())
    payload: dict[str, object] = {"results": records}
    if any(record.get("truncated") for record in records):
        payload["note"] = (
            "Hits marked truncated are cut. Read a code hit in full with "
            "cograph_read_node(node_id)."
        )
    if with_graph:
        payload["nodes"] = {
            key: node.model_dump(exclude={"summary"}) for key, node in response.nodes.items()
        }
    return payload


_CALLERS_SHOWN = 3


# Generated-wiki pages attached to one search: each is a title and a
# ~400-char lead, about 120 tokens. The whole page is a cograph_wiki_page call.
_WIKI_PAGES_PER_SEARCH = 2


async def _wiki_pages(
    session: AsyncSession,
    *,
    repository_id: UUID,
    hits: dict[str, str],
    query: str,
) -> list[dict[str, str]]:
    """Generated-wiki pages about a search's code hits (`hits`: node id -> file).

    A page cites the code it describes. It qualifies when it cites a hit or
    the hit's file, or when its title and listed questions share two query
    words (one, for a one-word query). With none of these, a page whose
    cited code shares a folder with two or more hits is the fallback; one
    shared folder alone is too loose, a flat folder holds unrelated code.

    A page citing an exact hit only cites its file too, so the file count
    already ranks it; exact hits break ties. A wiki is ~20 pages, so ranking
    reads only titles and citations; the content loads for the winners alone.
    """

    rows = (
        await session.execute(
            select(
                Document.slug,
                Document.title,
                Document.source_node_ids,
                Document.citations,
                Document.quality,
            ).where(
                Document.repository_id == repository_id,
                Document.doc_type == "wiki",
            )
        )
    ).all()
    if not rows:
        return []
    files = [path for path in hits.values() if path]
    words = set(extract_query_terms(query))
    need = min(2, len(words))
    primary: list[tuple[int, int, int, str, str]] = []
    fallback: list[tuple[int, int, int, str, str]] = []
    for row in rows:
        page_files = {
            cite.get("file_path")
            for cite in row.citations or []
            if isinstance(cite, dict) and cite.get("kind") == "node"
        }
        page_dirs = {posixpath.dirname(path) for path in page_files if path} - {""}
        same_file = sum(path in page_files for path in files)
        cited = len(hits.keys() & set(row.source_node_ids or []))
        quality = row.quality if isinstance(row.quality, dict) else {}
        about = " ".join([row.title, *map(str, quality.get("covers_questions") or [])])
        named = len(words.intersection(extract_query_terms(about, max_terms=10_000)))
        if same_file or cited or (need and named >= need):
            primary.append((-same_file, -cited, -named, row.slug, row.title))
            continue
        same_dir = sum(posixpath.dirname(path) in page_dirs for path in files)
        if same_dir >= 2:
            fallback.append((-same_dir, 0, -named, row.slug, row.title))
    picked = sorted(primary or fallback)[:_WIKI_PAGES_PER_SEARCH]
    if not picked:
        return []
    content = dict(
        (
            await session.execute(
                select(Document.slug, Document.content).where(
                    Document.repository_id == repository_id,
                    Document.doc_type == "wiki",
                    Document.slug.in_([slug for *_, slug, _ in picked]),
                )
            )
        ).all()
    )
    return [
        {
            "page": slug,
            "title": title,
            "lead": extract_lead(content.get(slug) or "", max_chars=400),
        }
        for *_, slug, title in picked
    ]


def _code_hits(response: RetrievalResponse) -> dict[str, str]:
    """Code hits as node id (the form wiki citations store) -> file path."""
    return {
        str(row.provenance.node_id): row.provenance.file_path or ""
        for row in response.results
        if row.provenance.node_id is not None
    }


async def _called_by(
    session: AsyncSession, node_ids: list[UUID]
) -> dict[UUID, tuple[list[dict[str, Any]], int]]:
    """The first callers of each node and how many there are, in one query.

    Rides `idx_code_edges_target (target_node_id, edge_type)`. A node with
    no callers is missing from the result. Callers sort by path, then line.
    """

    if not node_ids:
        return {}
    # One caller can reach a node through two spellings of its name: count
    # it once. A recursive call is not a caller.
    pairs = (
        select(
            CodeEdge.target_node_id.label("callee"),
            CodeEdge.source_node_id.label("caller"),
        )
        .where(
            CodeEdge.target_node_id.in_(node_ids),
            CodeEdge.edge_type == CodeEdgeType.CALLS.value,
            CodeEdge.source_node_id != CodeEdge.target_node_id,
        )
        .distinct()
        .subquery()
    )
    ranked = (
        select(
            pairs.c.callee,
            CodeNode.id,
            CodeNode.qualified_name,
            CodeNode.file_path,
            CodeNode.start_line,
            CodeNode.end_line,
            func.count().over(partition_by=pairs.c.callee).label("total"),
            func.row_number()
            .over(
                partition_by=pairs.c.callee,
                order_by=(CodeNode.file_path, CodeNode.start_line, CodeNode.id),
            )
            .label("rank"),
        )
        .join(CodeNode, CodeNode.id == pairs.c.caller)
        .subquery()
    )
    rows = await session.execute(
        select(ranked).where(ranked.c.rank <= _CALLERS_SHOWN).order_by(ranked.c.rank)
    )
    found: dict[UUID, tuple[list[dict[str, Any]], int]] = {}
    for row in rows:
        callers, _ = found.get(row.callee, ([], 0))
        callers.append(
            {
                "node_id": row.id,
                "name": row.qualified_name,
                "at": _at(row.file_path, row.start_line, row.end_line),
            }
        )
        found[row.callee] = (callers, row.total)
    return found


def _at(path: str | None, start: int | None, end: int | None) -> str | None:
    return path if start is None else f"{path}:{start}-{end}"


def _add_doc(
    places: dict[object, dict[str, Any]],
    path: str | None,
    heading_path: list[str] | None,
    text: str,
    truncated: bool,
) -> None:
    key = (path, tuple(heading_path or ()))
    record = places.get(key)
    if record is None:
        places[key] = {
            "heading_path": heading_path,
            "at": path,
            "text": text,
            "truncated": truncated or None,
        }
    elif text not in record["text"]:
        # Two chunks of one long section share the citation: one record.
        record["text"] += " … " + text
        record["truncated"] = record["truncated"] or truncated or None


async def retrieve_payload(
    *,
    services: MCPServices,
    query: str,
    repository_id: UUID,
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
) -> dict[str, object]:
    async with services.session_manager.session() as session:
        # retrieve_composite answers a not-ready repository with no rows,
        # which the agent cannot tell from "nothing matched".
        await require_ready_repository(session=session, repository_id=repository_id)
        embed_provider = services.embed_provider
        if embed_provider is None:
            runtime_providers = await build_runtime_providers(
                session=session,
                settings=services.settings,
            )
            embed_provider = runtime_providers.embed_provider
        response = await retrieve_composite(
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
        node_ids = _node_ids(response)
        called_by = await _called_by(session, node_ids)
        wiki = await _wiki_pages(
            session,
            repository_id=repository_id,
            hits=_code_hits(response),
            query=query,
        )
    payload = compact_hits(
        response,
        with_dates=any(when is not None for when in (as_of, since, until)),
        with_graph=include_graph,
        called_by=called_by,
    )
    if wiki:
        payload["wiki"] = wiki
    return payload


def _node_ids(response: RetrievalResponse) -> list[UUID]:
    return list({row.provenance.node_id for row in response.results} - {None})


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
            raise ToolError("NOT_FOUND: Graph node not found")

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
        # No AST row: it only repeated the signature that opens the body.
        requested_layers: set[RetrievalLayer] = {RetrievalLayer.CODE}
        if with_summary:
            requested_layers.add(RetrievalLayer.AST_SUMMARY)
        response = await services.context_builder.build(
            session,
            chunks=[chunk],
            requested_layers=requested_layers,
            repository_id=repository_id,
            include_chunks=with_linked_docs,
            include_graph=with_graph,
            include_scores=False,
            snippet_chars=snippet_chars,
        )
    # Search snippets collapse whitespace; a body read for its code keeps the
    # real lines, cut on a line boundary so the rest continues by line number.
    for result in response.results:
        if result.layer == RetrievalLayer.CODE:
            result.snippet, result.content_truncated = _head_lines(
                node.content or "", snippet_chars
            )
    response.total_tokens_estimate = sum(len(r.snippet) for r in response.results) // 4
    return response


def _head_lines(text: str, chars: int) -> tuple[str, bool]:
    if len(text) <= chars:
        return text, False
    cut = text.rfind("\n", 0, chars)
    return text[: cut if cut > 0 else chars], True


async def search_code_payload(
    *,
    services: MCPServices,
    repository_id: UUID,
    query: str,
    top_k: int,
    path_prefix: str = "",
) -> dict[str, object]:
    async with services.session_manager.session() as session:
        await require_ready_repository(session=session, repository_id=repository_id)
        lexical_hits = await services.lexical.search(
            session,
            store="code",
            query_text=query,
            repository_id=repository_id,
            top_k=top_k,
            path_prefix=path_prefix,
        )
        symbol_hits = await services.symbol.search(
            session,
            query_text=query,
            repository_id=repository_id,
            top_k=top_k,
            path_prefix=path_prefix,
        )
        merged = rrf_merge_streams(
            [lexical_hits, symbol_hits],
            k=services.settings.retrieval.rrf_k,
            candidate_cap=top_k,
            stream_names=["lexical", "symbol"],
        )
        # A whole-file module node matches any name in its file, so it took a
        # slot from the function the agent was looking for. Drop it before
        # the cut: both streams were asked for top_k, so real hits fill in.
        named = [
            hit
            for hit in merged
            if not str(hit.metadata.get("qualified_name", "")).endswith("#module")
        ]
        response = await services.context_builder.build(
            session,
            chunks=named[:top_k],
            requested_layers={RetrievalLayer.AST},
            repository_id=repository_id,
            include_chunks=False,
            include_graph=False,
            include_scores=False,
        )
        node_ids = _node_ids(response)
        called_by = await _called_by(session, node_ids)
        wiki = await _wiki_pages(
            session,
            repository_id=repository_id,
            hits=_code_hits(response),
            query=query,
        )
    payload = compact_hits(response, called_by=called_by)
    if wiki:
        payload["wiki"] = wiki
    return payload


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
            raise ToolError("NOT_FOUND: Graph node not found")
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


# A whole document longer than this comes back cut on a chunk boundary. Same
# budget as a full wiki page: the answer also repeats code blocks and tables.
_DOCUMENT_MAX_CHARS = 12000


def _document_head(
    chunks: list[Any],
) -> tuple[str, Any] | None:
    """Leading whole chunks that fit the budget, and the first chunk left out.

    None when every chunk fits: then the document goes out whole. A first
    chunk that alone is over the budget (one huge table or code block) is cut
    on a line, and it is also the chunk to read next, in full.
    """

    parts: list[str] = []
    size = 0
    for chunk in chunks:
        size += len(chunk.content) + 2
        if size > _DOCUMENT_MAX_CHARS:
            if not parts:
                head, _ = truncate_markdown(chunk.content, max_chars=_DOCUMENT_MAX_CHARS)
                return head, chunk
            return "\n\n".join(parts), chunk
        parts.append(chunk.content)
    return None


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
            raise ToolError("NOT_FOUND: Document not found")
        chunk_count = (
            await session.scalar(
                select(func.count(MdChunk.id)).where(MdChunk.document_id == document.id)
            )
            or 0
        )
        cut = None
        if len(document.content) > _DOCUMENT_MAX_CHARS:
            chunks = (
                await session.execute(
                    select(MdChunk.id, MdChunk.chunk_index, MdChunk.content)
                    .where(MdChunk.document_id == document.id)
                    .order_by(MdChunk.chunk_index)
                )
            ).all()
            cut = _document_head(chunks)
        head, next_chunk = cut or (document.content, None)
        payload: dict[str, object] = {
            "id": document.id,
            "collection_id": document.collection_id,
            "collection_name": collection.name,
            "source_key": document.source_key,
            "title": document.title,
            "content": head,
            "content_truncated": next_chunk is not None,
            "bytes": document.bytes,
            "word_count": document.word_count,
            "line_count": document.line_count,
            "frontmatter": document.frontmatter,
            "chunk_count": chunk_count,
            "created_at": document.created_at,
            "updated_at": document.updated_at,
        }
        if next_chunk is None:
            payload.update(
                heading_tree=document.heading_tree,
                code_blocks=document.code_blocks,
                tables=document.tables,
                links=document.links,
            )
        else:
            # The parsed lists describe the whole document and copy its code
            # and tables, so they would undo the cut.
            payload.update(
                next_chunk_id=next_chunk.id,
                next_chunk_index=next_chunk.chunk_index,
                note=(
                    "Document cut on a chunk boundary. Continue with "
                    "cograph_read_chunk(chunk_id=next_chunk_id); every chunk "
                    "names its own next_chunk_id."
                ),
            )
        return payload


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
            raise ToolError("NOT_FOUND: Chunk not found")
        document = await session.get(MdDocument, chunk.document_id)
        # Lets an agent walk a cut document chunk by chunk; null after the last.
        next_chunk_id = await session.scalar(
            select(MdChunk.id)
            .where(
                MdChunk.document_id == chunk.document_id,
                MdChunk.chunk_index > chunk.chunk_index,
            )
            .order_by(MdChunk.chunk_index)
            .limit(1)
        )
        return {
            "collection_id": collection.id,
            "collection_name": collection.name,
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "heading_path": chunk.heading_path,
            "content": chunk.content,
            "next_chunk_id": next_chunk_id,
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
                # The summarized whole-wiki map (~2-3k tokens): every page's
                # lead prose, section headings, and covered questions. This is
                # the DEFAULT wiki surface over MCP. Full page bodies are not
                # pushed here to keep the resource cheap — an agent that needs a
                # page (or one section) verbatim pulls it on demand via the
                # cograph_wiki_page tool (see `full_page_tool` below).
                "wiki_form": "summarized",
                "full_page_tool": "cograph_wiki_page",
                "hint": (
                    "This is the summarized wiki: per page a lead overview, "
                    "section headings, and the reader-questions it answers. "
                    "It is the default surface and is enough for conceptual / "
                    "architectural framing. When one page warrants the full "
                    "prose, diagrams, or code samples, call "
                    "cograph_wiki_page(repository=<slug>, page=<page-slug>) — "
                    "or pass section=<heading> to pull just that section. Do "
                    "not pull full pages by default; start from this summary."
                ),
                "compact": compact,
            }
        )


def _wiki_resource_uris(
    *,
    repository: Repository,
) -> dict[str, str]:
    # Deliberately no per-page URI: the summarized map is the only wiki form
    # *advertised* as a resource (so it, and only it, lands in an agent's
    # context by default). Full page bodies are reachable, but pull-only —
    # through the cograph_wiki_page tool, which an agent calls deliberately
    # rather than receiving up front. No graph URI either (the whole-repo graph
    # snapshot was a 40-60k-token dump; search_code / retrieve / read_node /
    # related cover every agent need).
    slug_path = f"{repository.host}/{repository.owner}/{repository.name}"
    return {
        "tree": f"cograph://repo/{slug_path}/wiki",
    }


# A full wiki page is an on-demand pull, not a pushed resource. Cap the body so
# a single call can't blow an agent's budget; if `content_truncated` is true the
# agent should narrow to one `section` (sections are far smaller than a whole
# page). The cap is enforced verbatim (line-boundary cut, structure preserved) —
# not via the query-snippet excerpter, which would flatten newlines and clamp.
_WIKI_PAGE_MAX_CHARS = 12000


def _approx_tokens(text: str) -> int:
    # Coarse 4-chars-per-token estimate of a page, not an accounting figure:
    # no tokenizer on the read path.
    return (len(text) + 3) // 4


async def wiki_page_payload(
    *,
    services: MCPServices,
    repository: Repository,
    page: str,
    section: str | None = None,
    max_chars: int = _WIKI_PAGE_MAX_CHARS,
) -> object:
    async with services.session_manager.session() as session:
        await require_ready_repository(
            session=session,
            repository_id=repository.id,
        )
        wiki_page = await services.wiki_queries.get_page_by_slug(
            session=session,
            repository_id=repository.id,
            slug=page,
        )
    if wiki_page is None:
        raise ToolError(
            f"NOT_FOUND: No wiki page '{page}' for this repository"
        )

    body = wiki_page.content
    section_title: str | None = None
    if section is not None and section.strip():
        extracted = extract_section(wiki_page.content, section)
        if extracted is None:
            available = extract_sections(wiki_page.content)
            listed = ", ".join(available) if available else "(none)"
            raise ToolError(
                f"NOT_FOUND: Section '{section}' not in wiki page '{page}'. "
                f"Available sections: {listed}"
            )
        body = extracted
        # Echo the canonical heading (from the matched heading line) rather than
        # the agent's raw input, which may differ in case/whitespace.
        section_title = extracted.splitlines()[0].lstrip("#").strip() or section

    content, truncated = truncate_markdown(body, max_chars=max_chars)
    return encode_payload(
        {
            "repository_slug": (
                f"{repository.host}/{repository.owner}/{repository.name}"
            ),
            "wiki_slug": wiki_page.slug,
            "title": wiki_page.title,
            "section": section_title,
            "content": content,
            "content_truncated": truncated,
            "tokens_estimate": _approx_tokens(content),
            "note": (
                "Full generated-wiki page body, fetched on demand and returned "
                "verbatim. The default wiki surface is the summarized compact "
                "map (the cograph_wiki_tree resource); pull full pages only "
                "when that summary is too terse. If content_truncated is true, "
                "request a single section= to get the rest in full."
            ),
        }
    )


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
        # A failed re-sync snaps back to READY, so ERROR means the first
        # index failed and nothing is indexed.
        why = {
            RepositoryStatus.ERROR: "its first index failed, nothing is indexed",
            RepositoryStatus.DELETING: "it is being deleted",
        }.get(repository.status, "its indexing has not finished yet")
        raise ToolError(f"REPO_NOT_READY: Repository is not ready: {why}")
    return repository


async def require_repository(
    *,
    session: AsyncSession,
    repository_id: UUID,
) -> Repository:
    repository = await session.get(Repository, repository_id)
    if repository is None:
        raise ToolError("NOT_FOUND: Repository not found")
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
        raise ToolError(
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
        raise ToolError("NOT_FOUND: Repository not found")
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
        raise ToolError(
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
